"""The media library: pictures and videos, stored next to the database.

Files live in <db folder>/media (data/media locally, /data/media in Docker's
volume) under a random id. The type is decided by the file's first bytes, not
by what the browser claims: JPEG, PNG and GIF pictures (up to 8 MB) and MP4 or
MOV videos (up to 1 GB). Uploads are streamed to a temporary file first
(routes/api.py), so a large video never sits in memory. A video's size,
shape and length are read from the file (app/mediainfo.py). See ADR 0011.
"""

from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from typing import Any

from ..db import Database
from ..platforms import ImageFile, VideoFile
from ..timeutil import now_iso
from .errors import Conflict, InvalidInput, NotFound
from ..mediainfo import image_size, sniff_video, video_info

MAX_BYTES = 8 * 1024 * 1024            # pictures
MAX_VIDEO_BYTES = 1024 * 1024 * 1024   # videos (LinkedIn takes up to 500 MB, YouTube far more)
SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
_ID = re.compile(r"^[0-9a-f]{32}$")
GONE = "That picture or video isn't in the library any more. Attach it again."


def sniff_image(data: bytes) -> str | None:
    """The image type from the file's first bytes, or None if it isn't JPEG, PNG or GIF."""
    return next((mime for magic, mime in SIGNATURES if data.startswith(magic)), None)


def _clean_name(name: str | None) -> str:
    base = os.path.basename(name or "").strip()
    return re.sub(r"[\x00-\x1f]", "", base)[:120]


class MediaService:
    def __init__(self, db: Database, folder: Path):
        self.db = db
        self.folder = folder

    # ---- adding ----

    def temp_path(self) -> Path:
        """Where an upload is streamed to before store() checks and keeps it."""
        self.folder.mkdir(parents=True, exist_ok=True)
        return self.folder / f".upload-{secrets.token_hex(8)}"

    def save(self, data: bytes, name: str | None = None) -> dict[str, Any]:
        """Store bytes that are already in memory (small pictures, cropper output, tests)."""
        tmp = self.temp_path()
        tmp.write_bytes(data)
        return self.store(tmp, name)

    def store(self, tmp: Path, name: str | None = None) -> dict[str, Any]:
        """Check an uploaded file and keep it in the library. The temporary file is always removed."""
        try:
            size = tmp.stat().st_size
            if not size:
                raise InvalidInput("That file is empty.")
            with open(tmp, "rb") as f:
                head = f.read(64 * 1024)
            mime = sniff_image(head)
            kind = "image" if mime else "video"
            if not mime:
                mime = sniff_video(head)
            if not mime:
                raise InvalidInput("Only JPEG, PNG and GIF pictures and MP4 or MOV videos can be added.")
            if kind == "image" and size > MAX_BYTES:
                raise InvalidInput(f"Pictures can be up to 8 MB; this one is {size / 1048576:.1f} MB.")
            if kind == "video" and size > MAX_VIDEO_BYTES:
                raise InvalidInput(f"Videos can be up to 1 GB; this one is {size / 1073741824:.1f} GB.")
            media_id = secrets.token_hex(16)
            path = self.folder / media_id
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)

        width = height = duration = None
        if kind == "image":
            width, height = image_size(path.read_bytes()) or (None, None)
        else:
            info = video_info(path)
            width, height, duration = info.width, info.height, info.duration_ms
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO media (id, mime, size, created_at, kind, name, width, height, duration_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (media_id, mime, size, now_iso(), kind, _clean_name(name), width, height, duration),
            )
        return self.get(media_id)

    # ---- reading ----

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["url"] = f"/api/media/{item['id']}"
        return item

    def get(self, media_id: str) -> dict[str, Any]:
        self.find(media_id)
        with self.db.connect() as conn:
            item = self._row(conn.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone())
        item["used_by"] = self.used_by(media_id)
        return item

    def list(self, kind: str | None = None) -> list[dict[str, Any]]:
        """The library, newest first, each with the posts that use it."""
        sql, args = "SELECT * FROM media", []
        if kind:
            sql += " WHERE kind=?"
            args.append(kind)
        with self.db.connect() as conn:
            items = [self._row(r) for r in conn.execute(sql + " ORDER BY created_at DESC", args)]
        for item in items:
            item["used_by"] = self.used_by(item["id"])
        return [i for i in items if (self.folder / i["id"]).exists()]

    def used_by(self, media_id: str) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT t.post_id, t.platform, t.status, p.scheduled_at FROM post_targets t "
                "JOIN posts p ON p.id = t.post_id "
                "WHERE json_extract(t.options, '$.image_id') = ? OR json_extract(t.options, '$.video_id') = ? "
                "ORDER BY p.scheduled_at DESC",
                (media_id, media_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def find(self, media_id: str | None, kind: str | None = None) -> tuple[Path, str]:
        """Path and type of a stored file. NotFound if it's gone (or isn't a `kind`)."""
        if not media_id or not _ID.match(media_id):  # ids are hex only: no path tricks
            raise NotFound(GONE)
        with self.db.connect() as conn:
            row = conn.execute("SELECT mime, kind FROM media WHERE id = ?", (media_id,)).fetchone()
        path = self.folder / media_id
        if row is None or not path.exists() or (kind and row["kind"] != kind):
            raise NotFound(GONE)
        return path, row["mime"]

    def exists(self, media_id: str | None, kind: str = "image") -> bool:
        try:
            self.find(media_id, kind)
        except NotFound:
            return False
        return True

    def load(self, media_id: str | None, alt: str = "") -> ImageFile:
        path, mime = self.find(media_id, "image")
        return ImageFile(path.read_bytes(), mime, alt)

    def load_video(self, media_id: str | None, alt: str = "") -> VideoFile:
        path, mime = self.find(media_id, "video")
        with self.db.connect() as conn:
            row = conn.execute("SELECT size, width, height, duration_ms FROM media WHERE id=?", (media_id,)).fetchone()
        return VideoFile(str(path), mime, row["size"], row["width"], row["height"], row["duration_ms"], alt)

    # ---- removing ----

    def delete(self, media_id: str) -> None:
        """Remove from the library. Refused while a post that hasn't gone out yet still uses it."""
        self.find(media_id)
        waiting = [u for u in self.used_by(media_id) if u["status"] in ("scheduled", "publishing")]
        if waiting:
            raise Conflict(f"A scheduled post still uses this (post {waiting[0]['post_id']}). "
                           "Remove it from that post first.")
        (self.folder / media_id).unlink(missing_ok=True)
        with self.db.connect() as conn:
            conn.execute("DELETE FROM media WHERE id=?", (media_id,))
