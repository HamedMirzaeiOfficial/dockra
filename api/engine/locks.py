import threading
from contextlib import contextmanager

from django.db import connection

_local = threading.Lock()


@contextmanager
def app_lock(app_id: int):
    """Serialize container changes of one app across workers."""
    if connection.vendor != "postgresql":
        with _local:
            yield
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s, %s)", [7301, int(app_id)])
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [7301, int(app_id)])
