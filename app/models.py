import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Set, Tuple


class SeenRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(os.path.dirname(db_path)).mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS assets_seen (
                    asset_id TEXT PRIMARY KEY,
                    first_seen_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS exhausted_pages (
                    page INTEGER PRIMARY KEY
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS exhausted_page_states (
                    filter_mode TEXT NOT NULL,
                    page INTEGER NOT NULL,
                    PRIMARY KEY (filter_mode, page)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def mark_seen(self, asset_ids: Iterable[str]) -> None:
        if not asset_ids:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO assets_seen(asset_id, first_seen_at)
                VALUES (?, datetime('now'))
                """,
                ((aid,) for aid in asset_ids),
            )
            conn.commit()

    def reset(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM assets_seen")
            conn.execute("DELETE FROM exhausted_pages")
            conn.execute("DELETE FROM exhausted_page_states")
            conn.execute("DELETE FROM meta")
            conn.commit()
        with self._connect() as conn:
            conn.execute("VACUUM")

    def get_seen_ids(self) -> Set[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT asset_id FROM assets_seen").fetchall()
        return {r[0] for r in rows}

    def count_seen(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM assets_seen").fetchone()
        return int(row[0]) if row else 0

    def mark_page_exhausted(self, page: int, filter_mode: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO exhausted_page_states(filter_mode, page)
                VALUES (?, ?)
                """,
                (str(filter_mode), int(page)),
            )
            conn.commit()

    def get_exhausted_pages(self, filter_mode: str) -> Set[int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT page FROM exhausted_page_states WHERE filter_mode=?",
                (str(filter_mode),),
            ).fetchall()
        return {int(r[0]) for r in rows}

    def clear_exhausted_pages(self, filter_mode: str | None = None) -> None:
        with self._connect() as conn:
            if filter_mode is None:
                conn.execute("DELETE FROM exhausted_pages")
                conn.execute("DELETE FROM exhausted_page_states")
            else:
                conn.execute(
                    "DELETE FROM exhausted_page_states WHERE filter_mode=?",
                    (str(filter_mode),),
                )
            conn.commit()

    def set_meta(self, key: str, value: str) -> None:
        self.set_meta_many({key: value})

    def set_meta_many(self, values: dict[str, str]) -> None:
        if not values:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO meta(key, value)
                VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                ((str(key), str(value)) for key, value in values.items()),
            )
            conn.commit()

    def get_meta(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_total_pages_record(self, total_pages: int) -> None:
        self.set_meta("total_pages", str(int(total_pages)))

    def get_total_pages_record(self) -> int | None:
        val = self.get_meta("total_pages")
        try:
            return int(val) if val is not None else None
        except Exception:
            return None

    def get_counter(self, key: str) -> int:
        val = self.get_meta(key)
        try:
            return int(val) if val is not None else 0
        except Exception:
            return 0

    def incr_counter(self, key: str, delta: int) -> int:
        """Atomically adjust a non-negative integer counter and return its value."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO meta(key, value)
                VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value=CAST(
                    MAX(0, CAST(meta.value AS INTEGER) + ?) AS TEXT
                )
                """,
                (key, str(max(0, int(delta))), int(delta)),
            )
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            conn.commit()
        try:
            return int(row[0]) if row else 0
        except Exception:
            return 0

    def reset_tracking(self) -> None:
        """
        Clear exhausted pages and counters/meta, while keeping UI settings.
        """
        with self._connect() as conn:
            conn.execute("DELETE FROM exhausted_pages")
            conn.execute("DELETE FROM exhausted_page_states")
            conn.execute(
                "DELETE FROM meta WHERE key NOT IN ('ui_count', 'ui_filter', 'ui_theme')"
            )
            conn.commit()

    def set_ui_settings(self, count: int, filter_mode: str, theme: str) -> None:
        self.set_meta_many(
            {
                "ui_count": str(int(count)),
                "ui_filter": str(filter_mode),
                "ui_theme": str(theme),
            }
        )

    def get_ui_settings(
        self, default_count: int, default_filter: str, default_theme: str
    ) -> Tuple[int, str, str]:
        count = self.get_meta("ui_count")
        if count is None:
            rows = self.get_meta("ui_rows")
            cols = self.get_meta("ui_cols")
            try:
                if rows is not None and cols is not None:
                    count = str(int(rows) * int(cols))
            except Exception:
                count = None
        filter_mode = self.get_meta("ui_filter") or default_filter
        theme = self.get_meta("ui_theme") or default_theme
        try:
            count_val = int(count) if count is not None else default_count
        except Exception:
            count_val = default_count
        return count_val, filter_mode, theme

    def set_prev_round(self, payload: dict, ready: bool | None = None) -> None:
        values = {"prev_round": json.dumps(payload, ensure_ascii=False)}
        if ready is not None:
            values["prev_ready"] = "1" if ready else "0"
        self.set_meta_many(values)

    def get_prev_round(self) -> dict | None:
        val = self.get_meta("prev_round")
        if not val:
            return None
        try:
            return json.loads(val)
        except Exception:
            return None

    def set_prev_ready(self, ready: bool) -> None:
        self.set_meta("prev_ready", "1" if ready else "0")

    def is_prev_ready(self) -> bool:
        return self.get_meta("prev_ready") == "1"

    def clear_prev_round(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM meta WHERE key IN ('prev_round', 'prev_ready')")
            conn.commit()

    def set_forward_round(self, payload: dict, state: int | None = None) -> None:
        values = {"forward_round": json.dumps(payload, ensure_ascii=False)}
        if state is not None:
            values["forward_state"] = str(int(state))
        self.set_meta_many(values)

    def get_forward_round(self) -> dict | None:
        val = self.get_meta("forward_round")
        if not val:
            return None
        try:
            return json.loads(val)
        except Exception:
            return None

    def set_forward_state(self, state: int) -> None:
        self.set_meta("forward_state", str(int(state)))

    def get_forward_state(self) -> int:
        val = self.get_meta("forward_state")
        try:
            return int(val) if val is not None else 0
        except Exception:
            return 0

    def clear_forward_round(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM meta WHERE key IN ('forward_round', 'forward_state')")
            conn.commit()

    def set_current_round(self, payload: dict, ready: bool | None = None) -> None:
        values = {"current_round": json.dumps(payload, ensure_ascii=False)}
        if ready is not None:
            values["current_ready"] = "1" if ready else "0"
        self.set_meta_many(values)

    def get_current_round(self) -> dict | None:
        val = self.get_meta("current_round")
        if not val:
            return None
        try:
            return json.loads(val)
        except Exception:
            return None

    def set_current_ready(self, ready: bool) -> None:
        self.set_meta("current_ready", "1" if ready else "0")

    def is_current_ready(self) -> bool:
        return self.get_meta("current_ready") == "1"

    def clear_current_round(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM meta WHERE key IN ('current_round', 'current_ready')")
            conn.commit()
