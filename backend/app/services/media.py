"""Images attached in the composer, stored next to the database.

Files live in <db folder>/media (data/media locally, /data/media in Docker's
volume) under a random id. The type is decided by the file's first bytes, not
by what the browser claims, so only real JPEG, PNG and GIF files are kept.
"""

from __future__ import annotations

import re
import secrets
from pathlib import Path
from typing import Any

from ..db import Database
from ..platforms import ImageFile
from ..timeutil import now_iso
from .errors import InvalidInput, NotFound

MAX_BYTES = 8 * 1024 * 1024
SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
_ID = re.compile(r"^[0-9a-f]{32}$")


def sniff_image(data: bytes) -> str | None:
    """The image type from the file's first bytes, or None if it isn't JPEG, PNG or GIF."""
    return next((mime for magic, mime in SIGNATURES if data.startswith(magic)), None)


class MediaService:
    def __init__(self, db: Database, folder: Path):
        self.db = db
        self.folder = folder

    def save(self, data: bytes) -> dict[str, Any]:
        if not data:
            raise InvalidInput("That file is empty.")
        if len(data) > MAX_BYTES:
            raise InvalidInput(f"Images can be up to 8 MB; this one is {len(data) / 1048576:.1f} MB.")
        mime = sniff_image(data)
        if not mime:
            raise InvalidInput("Only JPEG, PNG and GIF images can be attached.")
        media_id = secrets.token_hex(16)
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / media_id).write_bytes(data)
        with self.db.connect() as conn:
            conn.execute("INSERT INTO media (id, mime, size, created_at) VALUES (?, ?, ?, ?)",
                         (media_id, mime, len(data), now_iso()))
        return {"id": media_id, "mime": mime, "size": len(data), "url": f"/api/media/{media_id}"}

    def find(self, media_id: str | None) -> tuple[Path, str]:
        """Path and type of a stored image. NotFound if it's gone."""
        if not media_id or not _ID.match(media_id):  # ids are hex only: no path tricks
            raise NotFound("That image isn't available any more. Attach it again.")
        with self.db.connect() as conn:
            row = conn.execute("SELECT mime FROM media WHERE id = ?", (media_id,)).fetchone()
        path = self.folder / media_id
        if row is None or not path.exists():
            raise NotFound("That image isn't available any more. Attach it again.")
        return path, row["mime"]

    def exists(self, media_id: str | None) -> bool:
        try:
            self.find(media_id)
        except NotFound:
            return False
        return True

    def load(self, media_id: str | None, alt: str = "") -> ImageFile:
        path, mime = self.find(media_id)
        return ImageFile(path.read_bytes(), mime, alt)
