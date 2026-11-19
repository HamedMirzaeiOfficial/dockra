import os
import shutil
from decimal import Decimal

from django.conf import settings


class QuotaExceeded(Exception):
    pass


def _read_meminfo(path: str) -> int | None:
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    return None


def _memory_mb() -> int:
    for path in ("/host/proc/meminfo", "/proc/meminfo"):
        found = _read_meminfo(path)
        if found:
            return found
    if os.name == "nt":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys // (1024 * 1024))
    return 8192


def host_memory_used() -> int | None:
    """Memory actually used on the host (MemTotal - MemAvailable); reclaimable cache is not counted. Outside Docker: None."""
    if settings.DOCKRA_DRIVER != "docker":
        return None
    for path in ("/host/proc/meminfo", "/proc/meminfo"):
        if not os.path.isfile(path):
            continue
        fields = {}
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                if key in ("MemTotal", "MemAvailable"):
                    fields[key] = int(rest.split()[0]) // 1024
        if len(fields) == 2:
            return fields["MemTotal"] - fields["MemAvailable"]
    return None


def host_pressure(resource: str) -> float | None:
    """Share of the last minute during which at least one process waited for CPU or memory (PSI, some avg60)."""
    for path in (f"/host/proc/pressure/{resource}", f"/proc/pressure/{resource}"):
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("some "):
                    for part in line.split():
                        if part.startswith("avg60="):
                            return float(part[6:])
    return None


def _cpu_count() -> int:
    cpuinfo = "/host/proc/cpuinfo"
    if os.path.isfile(cpuinfo):
        with open(cpuinfo, encoding="utf-8") as handle:
            count = sum(1 for line in handle if line.startswith("processor"))
        if count:
            return count
    return os.cpu_count() or 2


def _disk_path() -> str:
    root = getattr(settings, "DOCKRA_DATA_ROOT", "") or ""
    if root and os.path.isdir(root):
        return root
    if os.path.isdir("/"):
        return "/"
    return str(settings.BASE_DIR)


def _disk_gb() -> int:
    return int(shutil.disk_usage(_disk_path()).total // (1024 ** 3))


def host_cpu_total() -> float:
    return float(settings.DOCKRA_HOST_CPU or _cpu_count())


def host_capacity() -> dict:
    cpu_total = host_cpu_total()
    memory_total = int(settings.DOCKRA_HOST_MEMORY_MB or _memory_mb())
    disk_total = int(settings.DOCKRA_HOST_DISK_GB or _disk_gb())
    reserved_cpu = float(settings.DOCKRA_RESERVED_CPU)
    reserved_memory = int(settings.DOCKRA_RESERVED_MEMORY_MB)
    reserved_disk = int(settings.DOCKRA_RESERVED_DISK_GB)
    return {
        "cpu_total": round(cpu_total, 2),
        "memory_mb_total": memory_total,
        "disk_gb_total": disk_total,
        "cpu_reserved": reserved_cpu,
        "memory_mb_reserved": reserved_memory,
        "disk_gb_reserved": reserved_disk,
        "cpu_allocatable": round(max(cpu_total - reserved_cpu, 0), 2),
        "memory_mb_allocatable": max(memory_total - reserved_memory, 0),
        "disk_gb_allocatable": max(disk_total - reserved_disk, 0),
        # Admission limit with overcommit; disk is never overcommitted because volumes are fully allocated with fallocate.
        "cpu_limit": round(max(cpu_total - reserved_cpu, 0) * settings.DOCKRA_CPU_OVERCOMMIT, 2),
        "memory_mb_limit": int(max(memory_total - reserved_memory, 0) * settings.DOCKRA_MEMORY_OVERCOMMIT),
        "cpu_overcommit": settings.DOCKRA_CPU_OVERCOMMIT,
        "memory_overcommit": settings.DOCKRA_MEMORY_OVERCOMMIT,
        "cpu_burst": settings.DOCKRA_CPU_BURST,
    }


def _active_usage(skip_app_id=None, skip_db_id=None):
    from apps_core.models import App
    from databases.models import Database

    cpu = memory = disk = 0.0
    running_apps = (App.Status.RUNNING, App.Status.DEPLOYING)
    for app in App.objects.prefetch_related("disks"):
        if app.id != skip_app_id:
            disk += sum(item.size_gb for item in app.disks.all())
        if app.id == skip_app_id or app.status not in running_apps:
            continue
        count = max(app.instances, 1)
        cpu += float(app.cpu) * count
        memory += app.memory_mb * count
    running_dbs = (Database.Status.RUNNING, Database.Status.PROVISIONING)
    for database in Database.objects.all():
        if database.id != skip_db_id:
            disk += database.disk_gb
        if database.id == skip_db_id or database.status not in running_dbs:
            continue
        cpu += float(database.cpu or 0)
        memory += database.memory_mb
    return cpu, memory, disk


def ensure_capacity(cpu, memory_mb, disk_gb=0, skip_app_id=None, skip_db_id=None):
    used_cpu, used_memory, used_disk = _active_usage(skip_app_id, skip_db_id)
    cap = host_capacity()
    need_cpu = used_cpu + float(cpu)
    need_memory = used_memory + int(memory_mb)
    need_disk = used_disk + int(disk_gb)
    problems = []
    if need_cpu > cap["cpu_limit"] + 1e-6:
        problems.append(f"CPU {need_cpu:.2f} of {cap['cpu_limit']:.2f} cores")
    if need_memory > cap["memory_mb_limit"]:
        problems.append(f"RAM {need_memory} of {cap['memory_mb_limit']} MB")
    if need_disk > cap["disk_gb_allocatable"]:
        problems.append(f"disk {need_disk} of {cap['disk_gb_allocatable']} GB")
    if not problems:
        problem = _real_memory_problem(need_memory)
        if problem:
            problems.append(problem)
    if problems:
        raise QuotaExceeded("Not enough server capacity: " + "; ".join(problems))


def _real_memory_problem(need_memory) -> str:
    """With overcommit the sum of limits exceeds RAM, so reservation growth is accepted only if real memory has room."""
    if settings.DOCKRA_MEMORY_OVERCOMMIT <= 1:
        return ""
    real = host_memory_used()
    if real is None:
        return ""
    # Resizing an app: only the difference from its current reservation counts as growth.
    grow = need_memory - _active_usage()[1]
    if grow <= 0:
        return ""
    total = _memory_mb()
    ceiling = total * settings.DOCKRA_MEMORY_REAL_PERCENT / 100
    # Apps usually use less than half of their memory limit; half of the growth is added to current real usage.
    if real + grow / 2 > ceiling:
        return f"Host memory: {real} of {total} MB in use, no room for {grow} MB more"
    return ""


def app_demand(app, instances=None):
    count = max(app.instances if instances is None else instances, 1)
    disk = sum(item.size_gb for item in app.disks.all())
    return float(app.cpu) * count, app.memory_mb * count, disk


def snapshot() -> dict:
    cap = host_capacity()
    cpu, memory, disk = _active_usage()
    cap["cpu_used"] = round(cpu, 2)
    cap["memory_mb_used"] = int(memory)
    cap["disk_gb_used"] = int(disk)
    cap["cpu_free"] = round(cap["cpu_limit"] - cpu, 2)
    cap["memory_mb_free"] = int(cap["memory_mb_limit"] - memory)
    cap["disk_gb_free"] = int(cap["disk_gb_allocatable"] - disk)
    # Real usage next to reservations, to show how much overcommit headroom is left.
    cap["memory_mb_real_used"] = host_memory_used()
    cap["cpu_pressure"] = host_pressure("cpu")
    cap["memory_pressure"] = host_pressure("memory")
    return cap


def parse_cpu(value, low="0.10", high="8") -> Decimal:
    try:
        cpu = Decimal(str(value))
    except Exception as exc:
        raise QuotaExceeded("Invalid CPU value") from exc
    if cpu < Decimal(low) or cpu > Decimal(high):
        raise QuotaExceeded(f"CPU must be between {low} and {high} cores")
    return cpu.quantize(Decimal("0.01"))


def parse_range(value, low, high, label) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise QuotaExceeded(f"Invalid {label.lower()}") from exc
    if number < low or number > high:
        raise QuotaExceeded(f"{label} must be between {low} and {high}")
    return number
