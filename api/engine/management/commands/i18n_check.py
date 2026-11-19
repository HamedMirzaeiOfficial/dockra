"""Verify that every translatable API string has a Persian entry in config/locale/fa.json.

Translatable strings are the literal first arguments of tr()/tr_lazy()/_h() and the labels of every model
choices class (they reach the panel through get_*_display(), wrapped in tr() by the presenters).

    python manage.py i18n_check            fail when an entry is missing
    python manage.py i18n_check --unused   also list Persian entries nothing uses
"""

import ast
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from config.i18n import catalog

SKIP_PARTS = {"migrations", "management", "__pycache__"}


def literal_keys(root: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if SKIP_PARTS & set(rel.parts) or rel.name.startswith("test"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("tr", "tr_lazy", "_h")
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                found.setdefault(node.args[0].value, f"{rel}:{node.lineno}")
    return found


def choice_keys() -> dict[str, str]:
    found: dict[str, str] = {}
    for model in apps.get_models():
        if model._meta.app_label not in ("accounts", "apps_core", "databases", "engine", "audit"):
            continue
        for field in model._meta.get_fields():
            for _value, label in getattr(field, "choices", None) or []:
                if isinstance(label, str):
                    found.setdefault(label, f"{model.__name__}.{field.name}")
    return found


class Command(BaseCommand):
    help = "Check that every API message has a Persian translation."

    def add_arguments(self, parser):
        parser.add_argument("--unused", action="store_true", help="List Persian entries no code uses.")

    def handle(self, *args, **options):
        keys = {**choice_keys(), **literal_keys(Path(settings.BASE_DIR))}
        fa = catalog("fa")
        missing = sorted((where, key) for key, where in keys.items() if key not in fa)
        if options["unused"]:
            for key in sorted(set(fa) - set(keys)):
                self.stdout.write(f"unused: {key}")
        if missing:
            for where, key in missing:
                self.stderr.write(f"{where}: {key}")
            raise CommandError(f"{len(missing)} API strings have no Persian translation")
        self.stdout.write(self.style.SUCCESS(f"i18n: {len(keys)} API strings, all translated."))
