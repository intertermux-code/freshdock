import json
import sqlite3
from typing import List, Optional


class Database:
    def __init__(self, db_path: str = "freshdock.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS watched (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                container TEXT,
                image TEXT,
                last_digest TEXT,
                UNIQUE(container, image)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TIMESTAMP DEFAULT (datetime('now')),
                container TEXT,
                image TEXT,
                old_digest TEXT,
                new_digest TEXT,
                status TEXT,
                details TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS manifest_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                image_key TEXT UNIQUE,
                digest TEXT,
                etag TEXT,
                cached_at TIMESTAMP DEFAULT (datetime('now'))
            )
            """
        )
        self.conn.commit()

    def watch(self, container: Optional[str] = None, image: Optional[str] = None, last_digest: Optional[str] = None):
        cur = self.conn.cursor()
        # SQLite treats NULLs as distinct in a UNIQUE constraint, so the
        # constraint alone does not stop a second watch of the same image.
        cur.execute(
            "SELECT 1 FROM watched WHERE container IS ? AND image IS ?",
            (container, image),
        )
        if cur.fetchone():
            return
        cur.execute(
            "INSERT INTO watched (container, image, last_digest) VALUES (?, ?, ?)",
            (container, image, last_digest),
        )
        self.conn.commit()

    def list_watched(self) -> List[dict]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM watched")
        return [dict(r) for r in cur.fetchall()]

    def remove(self, container: Optional[str] = None, image: Optional[str] = None):
        cur = self.conn.cursor()
        if container:
            cur.execute("DELETE FROM watched WHERE container = ?", (container,))
        if image:
            cur.execute("DELETE FROM watched WHERE image = ?", (image,))
        self.conn.commit()

    def update_last_digest(self, container: Optional[str] = None, image: Optional[str] = None, digest: Optional[str] = None):
        cur = self.conn.cursor()
        # A container names its own entry; an image only entry is the one
        # without a container. Matching on the image alone would rewrite the
        # digest of every other container that watches the same image.
        if container is not None:
            cur.execute(
                "UPDATE watched SET last_digest = ? WHERE container = ?",
                (digest, container),
            )
        else:
            cur.execute(
                "UPDATE watched SET last_digest = ? WHERE container IS NULL AND image = ?",
                (digest, image),
            )
        self.conn.commit()

    def record_history(self, container=None, image=None, old_digest=None, new_digest=None, status="ok", details=None):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO history (container, image, old_digest, new_digest, status, details) VALUES (?, ?, ?, ?, ?, ?)",
            (container, image, old_digest, new_digest, status, json.dumps(details) if details is not None else None),
        )
        self.conn.commit()

    def get_history(self, limit: int = 20) -> List[dict]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            if r.get("details"):
                try:
                    r["details"] = json.loads(r["details"])
                except Exception:
                    pass
        return rows

    def cache_manifest(self, image_key: str, digest: Optional[str] = None, etag: Optional[str] = None):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO manifest_cache (image_key, digest, etag, cached_at) VALUES (?, ?, ?, datetime('now'))",
            (image_key, digest, etag),
        )
        self.conn.commit()

    def get_manifest_cache(self, image_key: str) -> Optional[dict]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM manifest_cache WHERE image_key = ?", (image_key,))
        row = cur.fetchone()
        return dict(row) if row else None

    def close(self):
        self.conn.close()
