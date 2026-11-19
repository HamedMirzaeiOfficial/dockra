"""Chunked uploads of large files (database backups, disk archives) for the panel and CLI.

A single long request hits the timeout of every proxy or CDN on the way and restarts from zero after any network drop.
Here the file arrives in small chunks and a lost chunk is resent; then the real endpoint
(database import, restore, disk import or upload) picks up the same file with `upload=<id>`.
"""

import re
import time
import uuid
from pathlib import Path

from django.conf import settings
from rest_framework.response import Response
from rest_framework.views import APIView
from config.i18n import tr

CHUNK = 1 << 20
MAX_PIECE = 32 << 20
UPLOAD_ID = re.compile(r"[0-9a-f]{32}")
STALE = 24 * 3600


def folder() -> Path:
    path = Path(settings.MEDIA_ROOT) / "restore"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path(request, upload_id) -> Path | None:
    """The organization id is part of the file name so no other organization can touch it."""
    if not UPLOAD_ID.fullmatch(str(upload_id or "")):
        return None
    return folder() / f"upload-{request.membership.organization_id}-{upload_id}"


def find(request, upload_id, allow_empty=False) -> Path | None:
    """The completed file of an upload, or None if missing (or empty, except for a regular disk file)."""
    path = _path(request, upload_id)
    if path is None or not path.is_file() or (path.stat().st_size == 0 and not allow_empty):
        return None
    return path


MISSING = "Uploaded file not found; please upload again"


class UploadStart(APIView):
    """Start an upload: {"upload": id}. Abandoned uploads are removed after one day."""

    parser_classes = []

    def post(self, request):
        now = time.time()
        for old in folder().glob("upload-*"):
            try:
                if now - old.stat().st_mtime > STALE:
                    old.unlink()
            except OSError:
                pass
        upload_id = uuid.uuid4().hex
        _path(request, upload_id).touch()
        return Response({"upload": upload_id, "size": 0}, status=201)


class UploadPiece(APIView):
    """GET the received size (to resume); PUT ?offset=N appends a chunk to the file.
    offset must equal the current size exactly; otherwise 409 with size so the client resumes from there
    (e.g. the chunk arrived but the response was lost)."""

    parser_classes = []

    def _file(self, request, upload_id):
        path = _path(request, upload_id)
        return path if path is not None and path.is_file() else None

    def get(self, request, upload_id):
        path = self._file(request, upload_id)
        if path is None:
            return Response({"detail": tr("Upload not found")}, status=404)
        return Response({"upload": upload_id, "size": path.stat().st_size})

    def put(self, request, upload_id):
        path = self._file(request, upload_id)
        if path is None:
            return Response({"detail": tr("Upload not found; start over")}, status=404)
        length = int(request.META.get("CONTENT_LENGTH") or 0)
        if not 0 < length <= MAX_PIECE:
            return Response({"detail": tr("Invalid chunk size")}, status=400)
        try:
            offset = int(request.query_params.get("offset", ""))
        except ValueError:
            return Response({"detail": tr("offset is required")}, status=400)
        size = path.stat().st_size
        if offset != size:
            return Response({"detail": tr("Chunk offset does not match"), "size": size}, status=409)
        written = 0
        with path.open("r+b") as handle:
            handle.seek(offset)
            while written < length:
                chunk = request.stream.read(min(CHUNK, length - written))
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
            if written != length:
                handle.truncate(offset)
                return Response({"detail": tr("Chunk arrived incomplete"), "size": offset}, status=400)
        return Response({"upload": upload_id, "size": offset + written})
