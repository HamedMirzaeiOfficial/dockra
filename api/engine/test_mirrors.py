import subprocess
from unittest import mock

from django.test import SimpleTestCase, override_settings

from engine import drivers


def _proc(code: int) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, b"", b"")


class BaseImageTests(SimpleTestCase):
    def test_from_lines(self):
        text = (
            "# syntax=docker/dockerfile:1\n"
            "ARG V=3.11\n"
            "FROM --platform=linux/amd64 python:3.11-slim-bookworm AS deps\n"
            "FROM python:${V}\n"
            "FROM deps AS app\n"
            "FROM scratch\n"
            "FROM ghcr.io/org/tool:1\n"
            "from node:20-alpine\n"
            "FROM python:3.11-slim-bookworm\n"
        )
        self.assertEqual(
            drivers.base_images(text),
            ["python:3.11-slim-bookworm", "ghcr.io/org/tool:1", "node:20-alpine"],
        )

    def test_hub_path(self):
        self.assertEqual(drivers._hub_path("python:3.11"), "library/python:3.11")
        self.assertEqual(drivers._hub_path("dpage/pgadmin4:9"), "dpage/pgadmin4:9")
        self.assertEqual(drivers._hub_path("docker.io/library/nginx:alpine"), "library/nginx:alpine")
        self.assertIsNone(drivers._hub_path("ghcr.io/org/tool:1"))
        self.assertIsNone(drivers._hub_path("localhost:5000/x"))


@override_settings(DOCKRA_DOCKER_MIRRORS="https://mirror.local/ https://hub-mirror.example.org")
class EnsureImageTests(SimpleTestCase):
    def test_present_image_is_not_pulled(self):
        with mock.patch.object(drivers, "_run", return_value=_proc(0)) as run:
            drivers.ensure_image("python:3.11")
        self.assertEqual(run.call_count, 1)

    def test_falls_back_through_mirrors_and_retags(self):
        def fake(args, **kwargs):
            if args[:2] == ["docker", "pull"]:
                return _proc(0 if args[-1].startswith("hub-mirror.example.org/") else 1)
            if args[:3] == ["docker", "image", "inspect"]:
                return _proc(1)
            return _proc(0)

        with mock.patch.object(drivers, "_run", side_effect=fake) as run:
            drivers.ensure_image("python:3.11-slim-bookworm")
        calls = [c.args[0] for c in run.call_args_list]
        self.assertIn(["docker", "pull", "-q", "python:3.11-slim-bookworm"], calls)
        self.assertIn(["docker", "pull", "-q", "mirror.local/library/python:3.11-slim-bookworm"], calls)
        self.assertIn(
            ["docker", "tag", "hub-mirror.example.org/library/python:3.11-slim-bookworm", "python:3.11-slim-bookworm"],
            calls,
        )
        # A duplicate mirror is tried only once, and after a success the next mirror is not tried
        pulls = [c for c in calls if c[:2] == ["docker", "pull"]]
        self.assertEqual(len(pulls), 3)

    def test_other_registry_is_not_mirrored(self):
        with mock.patch.object(drivers, "_run", return_value=_proc(1)) as run:
            drivers.ensure_image("ghcr.io/org/tool:1")
        self.assertEqual(run.call_count, 2)
