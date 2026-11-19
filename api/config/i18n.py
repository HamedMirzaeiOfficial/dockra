"""Tiny translation layer for API messages, the same model as the panel's lib/i18n.ts.

English source text is the key: ``tr("App not found")`` returns the Persian entry from
``config/locale/fa.json`` when the active language is Persian, and the English text otherwise, so a
missing entry degrades to English instead of a raw key. Placeholders use ``{name}`` and are filled from
keyword arguments: ``tr("Disk {name} is full", name=disk.name)``.

The active language comes from Django's LocaleMiddleware (the panel sends ``Accept-Language``). Code that
runs outside a request, such as worker jobs, has no active language and therefore stays in English.
``python manage.py i18n_check`` verifies that every ``tr()`` string has a Persian entry.

Why not gettext? It needs the GNU gettext tools (``xgettext``/``msgfmt``) on every developer machine and
in the image, plus compiled ``.mo`` files in git. A JSON dictionary keeps one mental model for panel and
API and is easy to review in a pull request.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

from django.utils import translation
from django.utils.functional import lazy

LOCALE_DIR = Path(__file__).resolve().parent / "locale"
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


@lru_cache(maxsize=None)
def catalog(lang: str) -> dict[str, str]:
    path = LOCALE_DIR / f"{lang}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def active_language() -> str:
    return (translation.get_language() or "en").split("-")[0]


def tr(text: str, **values: object) -> str:
    """Translate ``text`` into the active language and fill its ``{name}`` placeholders."""
    lang = active_language()
    out = catalog(lang).get(text, text) if lang != "en" else text
    if not values:
        return out
    return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), out)


# For module-level constants such as choice labels: translated when rendered, not when imported.
tr_lazy = lazy(tr, str)
