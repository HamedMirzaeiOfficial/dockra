import json
import threading
import urllib.parse
import urllib.request

from django.conf import settings


def _post(url: str, body: bytes, content_type: str) -> None:
    req = urllib.request.Request(url, data=body, headers={"Content-Type": content_type}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=8).close()
    except OSError:
        pass


def notify(event: str, text: str, **extra) -> None:
    """Send the message to Telegram and/or a webhook; never delays or breaks the main job."""
    targets = []
    if settings.DOCKRA_TELEGRAM_BOT_TOKEN and settings.DOCKRA_TELEGRAM_CHAT_ID:
        url = f"{settings.DOCKRA_TELEGRAM_API}/bot{settings.DOCKRA_TELEGRAM_BOT_TOKEN}/sendMessage"
        body = urllib.parse.urlencode({"chat_id": settings.DOCKRA_TELEGRAM_CHAT_ID, "text": text}).encode()
        targets.append((url, body, "application/x-www-form-urlencoded"))
    if settings.DOCKRA_NOTIFY_WEBHOOK:
        body = json.dumps({"event": event, "text": text, **extra}, ensure_ascii=False).encode()
        targets.append((settings.DOCKRA_NOTIFY_WEBHOOK, body, "application/json"))
    for target in targets:
        threading.Thread(target=_post, args=target, daemon=True).start()
