from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings

from engine.models import Alert
from engine.resources import QuotaExceeded, ensure_capacity


@override_settings(DOCKRA_HOST_CPU=8, DOCKRA_CPU_BURST=3)
class CpuSharingTests(TestCase):
    def test_flags_give_weight_and_burst_ceiling(self):
        from engine.drivers import container_flags, cpu_flags

        self.assertEqual(cpu_flags("0.5"), ["--cpus", "1.50", "--cpu-shares", "1280"])
        # The burst limit never exceeds the total number of host cores.
        self.assertEqual(cpu_flags(4)[:2], ["--cpus", "8.00"])
        self.assertIn("--memory", container_flags(1, 512))
        with override_settings(DOCKRA_CPU_BURST=1):
            self.assertEqual(cpu_flags("0.25")[:2], ["--cpus", "0.25"])

    def test_usage_percent_is_against_burst_ceiling(self):
        from engine.drivers import DockerDriver

        app = SimpleNamespace(slug="shop", cpu="0.5")
        rows = {"shop": {"cpus": [75.0], "rams": [10.0], "sent": 0.0}}
        self.assertEqual(DockerDriver().usage(app, rows)["cpu"], 50)


@override_settings(
    DOCKRA_HOST_CPU=8, DOCKRA_HOST_MEMORY_MB=4096, DOCKRA_HOST_DISK_GB=100,
    DOCKRA_RESERVED_CPU=1, DOCKRA_RESERVED_MEMORY_MB=1024, DOCKRA_RESERVED_DISK_GB=20,
    DOCKRA_CPU_OVERCOMMIT=2, DOCKRA_MEMORY_OVERCOMMIT=1.2, DOCKRA_MEMORY_REAL_PERCENT=85,
)
class OvercommitTests(TestCase):
    def test_reservations_may_exceed_physical_capacity(self):
        ensure_capacity(14, 3686)
        with self.assertRaises(QuotaExceeded):
            ensure_capacity(14.5, 0)
        with self.assertRaises(QuotaExceeded):
            ensure_capacity(0, 3687)

    def test_real_memory_blocks_growth(self):
        with mock.patch("engine.resources.host_memory_used", return_value=3450), \
                mock.patch("engine.resources._memory_mb", return_value=4096):
            ensure_capacity(0, 60)
            with self.assertRaises(QuotaExceeded) as caught:
                ensure_capacity(0, 200)
            self.assertIn("Host memory", str(caught.exception))
            with override_settings(DOCKRA_MEMORY_OVERCOMMIT=1):
                ensure_capacity(0, 200)


@override_settings(DOCKRA_MEMORY_REAL_PERCENT=85, DOCKRA_CPU_PRESSURE_PERCENT=25)
class HostSharingAlertTests(TestCase):
    def test_real_memory_and_cpu_pressure_alerts(self):
        from engine.alerts import _check_host_sharing

        with mock.patch("engine.alerts.notify"), \
                mock.patch("engine.resources._memory_mb", return_value=4096), \
                mock.patch("engine.resources.host_memory_used", return_value=3900), \
                mock.patch("engine.resources.host_pressure", return_value=40.0):
            _check_host_sharing()
        firing = set(Alert.objects.filter(status=Alert.Status.FIRING).values_list("kind", flat=True))
        self.assertEqual(firing, {Alert.Kind.HOST_MEMORY, Alert.Kind.HOST_CPU})
        with mock.patch("engine.alerts.notify"), \
                mock.patch("engine.resources._memory_mb", return_value=4096), \
                mock.patch("engine.resources.host_memory_used", return_value=1000), \
                mock.patch("engine.resources.host_pressure", return_value=2.0):
            _check_host_sharing()
        self.assertFalse(Alert.objects.filter(status=Alert.Status.FIRING).exists())


class RouteReconnectTests(TestCase):
    def test_recreated_traefik_gets_app_networks_back(self):
        from engine.drivers import DockerDriver

        def fake_run(args, check=True, **kwargs):
            out = ""
            if args[:3] == ["docker", "network", "ls"]:
                out = "dockra-app-shop\ndockra-app-blog\n" if "label=dockra.app" in args else ""
            elif args[:2] == ["docker", "inspect"]:
                out = "dockra dockra-app-blog "
            return SimpleNamespace(returncode=0, stdout=out.encode(), stderr=b"")

        driver = DockerDriver()
        with mock.patch("engine.drivers._run", side_effect=fake_run), \
                mock.patch.object(DockerDriver, "_self_container", return_value={}), \
                mock.patch.object(DockerDriver, "_traefik_containers", return_value=["traefik1"]), \
                mock.patch.object(DockerDriver, "_connect") as connect:
            driver.reconnect_routes()
        connect.assert_called_once_with("dockra-app-shop", "traefik1")
