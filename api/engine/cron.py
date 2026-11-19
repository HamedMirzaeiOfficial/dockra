from datetime import datetime


def _match_field(expr: str, value: int) -> bool:
    if expr == "*":
        return True
    for part in expr.split(","):
        if part.startswith("*/"):
            step = int(part[2:])
            if step > 0 and value % step == 0:
                return True
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            if int(start) <= value <= int(end):
                return True
            continue
        if int(part) == value or (int(part) == 7 and value == 0):
            return True
    return False


def cron_matches(expression: str, when: datetime) -> bool:
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("A cron expression must have five fields")
    minute, hour, dom, month, dow = fields
    cron_dow = (when.weekday() + 1) % 7
    if not _match_field(minute, when.minute):
        return False
    if not _match_field(hour, when.hour):
        return False
    if not _match_field(month, when.month):
        return False
    dom_ok = _match_field(dom, when.day)
    dow_ok = _match_field(dow, cron_dow)
    if dom != "*" and dow != "*":
        return dom_ok or dow_ok
    return dom_ok and dow_ok


def split_cron(line: str) -> tuple[str, str]:
    parts = line.split()
    if len(parts) < 6:
        raise ValueError("Incomplete cron line")
    return " ".join(parts[:5]), " ".join(parts[5:])


_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


def validate(expression: str) -> None:
    """Raise if any field is malformed or outside its allowed range."""
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("A cron expression must have five fields")
    for expr, (low, high) in zip(fields, _BOUNDS, strict=True):
        for part in expr.split(","):
            if part == "*":
                continue
            if part.startswith("*/"):
                if not part[2:].isdigit() or int(part[2:]) < 1:
                    raise ValueError(part)
                continue
            ends = part.split("-", 1)
            if not all(item.isdigit() and low <= int(item) <= high for item in ends):
                raise ValueError(part)
            if len(ends) == 2 and int(ends[0]) > int(ends[1]):
                raise ValueError(part)
