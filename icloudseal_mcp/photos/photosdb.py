"""Read-only access to the local Photos library database.

Photos exposes no usable remote API; AppleScript automation of Photos.app is
slow and blocks on TCC prompts. The reliable path is reading the library's
SQLite catalog at
``~/Pictures/Photos Library.photoslibrary/database/Photos.sqlite`` (needs Full
Disk Access), opened read-only/immutable.

Note: with iCloud "Optimize Mac Storage", most originals live in iCloud and are
*not* on disk — this module reads metadata; export only works for originals
already downloaded locally.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

LIBRARY = Path.home() / "Pictures" / "Photos Library.photoslibrary"
PHOTOS_DB = LIBRARY / "database" / "Photos.sqlite"
ORIGINALS = LIBRARY / "originals"
_APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)


class PhotosAccessError(RuntimeError):
    """Raised when Photos.sqlite cannot be opened (usually missing Full Disk Access)."""


@dataclass(frozen=True)
class Album:
    title: str
    count: int


@dataclass(frozen=True)
class Asset:
    uuid: str
    directory: str
    filename: str
    date_iso: str
    kind: str       # "photo" or "video"
    favorite: bool


def _cocoa_to_iso(value: float | None) -> str:
    if not value:
        return ""
    try:
        return (_APPLE_EPOCH + timedelta(seconds=value)).astimezone().isoformat()
    except (OverflowError, OSError):
        return ""


def _connect() -> sqlite3.Connection:
    if not PHOTOS_DB.exists():
        raise PhotosAccessError(f"{PHOTOS_DB} not found.")
    try:
        con = sqlite3.connect(f"file:{PHOTOS_DB}?mode=ro&immutable=1", uri=True)
        con.row_factory = sqlite3.Row
        con.execute("SELECT 1 FROM ZASSET LIMIT 1")
        return con
    except sqlite3.OperationalError as exc:
        raise PhotosAccessError(
            "Cannot read Photos.sqlite. Grant Full Disk Access to your terminal/app: "
            "System Settings → Privacy & Security → Full Disk Access."
        ) from exc


def _album_join(con: sqlite3.Connection) -> tuple[str, str, str]:
    """Discover the album↔asset join table and its columns (version-robust)."""
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Z\\_%ASSETS' ESCAPE '\\'"
    ).fetchall()
    for (name,) in rows:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({name})")]
        album_col = next((c for c in cols if c.endswith("ALBUMS")), None)
        asset_col = next((c for c in cols if c.endswith("ASSETS") and "FOK" not in c), None)
        if album_col and asset_col:
            return name, album_col, asset_col
    raise PhotosAccessError("Could not locate the album-asset join table.")


def list_albums() -> list[Album]:
    with _connect() as con:
        jt, acol, ascol = _album_join(con)
        rows = con.execute(
            f"""
            SELECT al.ZTITLE AS title, COUNT(j.{ascol}) AS n
            FROM ZGENERICALBUM al
            LEFT JOIN {jt} j ON j.{acol} = al.Z_PK
            WHERE al.ZTITLE IS NOT NULL AND al.ZTITLE != ''
            GROUP BY al.Z_PK
            ORDER BY title COLLATE NOCASE
            """
        ).fetchall()
    return [Album(r["title"], r["n"]) for r in rows]


def _row_to_asset(r: sqlite3.Row) -> Asset:
    return Asset(
        uuid=r["ZUUID"],
        directory=r["ZDIRECTORY"] or "",
        filename=r["ZFILENAME"] or "",
        date_iso=_cocoa_to_iso(r["ZDATECREATED"]),
        kind="video" if r["ZKIND"] == 1 else "photo",
        favorite=bool(r["ZFAVORITE"]),
    )


def list_assets(
    *, album: str | None = None, limit: int = 50, kind: str | None = None,
    favorites_only: bool = False,
) -> list[Asset]:
    clauses = ["a.ZFILENAME IS NOT NULL", "a.ZTRASHEDSTATE = 0"]
    params: list[object] = []
    join = ""
    with _connect() as con:
        if album:
            jt, acol, ascol = _album_join(con)
            join = (
                f"JOIN {jt} j ON j.{ascol} = a.Z_PK "
                "JOIN ZGENERICALBUM al ON al.Z_PK = j." + acol
            )
            clauses.append("al.ZTITLE = ?")
            params.append(album)
        if kind == "photo":
            clauses.append("a.ZKIND = 0")
        elif kind == "video":
            clauses.append("a.ZKIND = 1")
        if favorites_only:
            clauses.append("a.ZFAVORITE = 1")
        params.append(limit)
        rows = con.execute(
            f"""
                 SELECT a.ZUUID, a.ZDIRECTORY, a.ZFILENAME, a.ZDATECREATED,
                     a.ZKIND, a.ZFAVORITE
            FROM ZASSET a {join}
            WHERE {" AND ".join(clauses)}
            ORDER BY a.ZDATECREATED DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_row_to_asset(r) for r in rows]


def stats() -> dict:
    with _connect() as con:
        total = con.execute("SELECT COUNT(*) FROM ZASSET WHERE ZTRASHEDSTATE = 0").fetchone()[0]
        photos = con.execute(
            "SELECT COUNT(*) FROM ZASSET WHERE ZKIND = 0 AND ZTRASHEDSTATE = 0"
        ).fetchone()[0]
        videos = con.execute(
            "SELECT COUNT(*) FROM ZASSET WHERE ZKIND = 1 AND ZTRASHEDSTATE = 0"
        ).fetchone()[0]
        favorites = con.execute(
            "SELECT COUNT(*) FROM ZASSET WHERE ZFAVORITE = 1 AND ZTRASHEDSTATE = 0"
        ).fetchone()[0]
        albums = con.execute(
            "SELECT COUNT(*) FROM ZGENERICALBUM WHERE ZTITLE IS NOT NULL AND ZTITLE != ''"
        ).fetchone()[0]
    return {
        "total": total, "photos": photos, "videos": videos,
        "favorites": favorites, "albums": albums,
    }


def find_local_original(asset: Asset) -> Path | None:
    """Resolve one exact downloaded original from its catalog directory and UUID."""
    if not ORIGINALS.exists() or not asset.directory or not asset.filename:
        return None
    root = ORIGINALS.resolve()
    candidate = (root / asset.directory / asset.filename).resolve()
    if candidate != root and root not in candidate.parents:
        raise PhotosAccessError("Photos catalog returned an invalid original path.")
    return candidate if candidate.is_file() else None
