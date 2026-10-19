from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class Store:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._init()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    node_id TEXT PRIMARY KEY,
                    short_name TEXT,
                    unlocked_at REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS queries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    node_id TEXT NOT NULL,
                    short_name TEXT,
                    prompt TEXT NOT NULL,
                    reply TEXT,
                    mode TEXT,
                    latency_ms INTEGER,
                    error TEXT,
                    kind TEXT
                );

                CREATE TABLE IF NOT EXISTS pushes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT,
                    node_id TEXT,
                    channel TEXT,
                    detail TEXT
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS subscriptions (
                    node_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    place TEXT NOT NULL DEFAULT 'both',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (node_id, kind)
                );

                CREATE TABLE IF NOT EXISTS node_labels (
                    node_id TEXT PRIMARY KEY,
                    alias TEXT,
                    note TEXT,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    user_label TEXT,
                    user_label_at REAL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS channel_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    channel TEXT NOT NULL,
                    channel_index INTEGER,
                    direction TEXT NOT NULL,
                    node_id TEXT,
                    text TEXT NOT NULL
                );
                """
            )
            # Lightweight migration for older DBs
            cols = {
                r["name"]
                for r in conn.execute("PRAGMA table_info(queries)").fetchall()
            }
            if "kind" not in cols:
                conn.execute("ALTER TABLE queries ADD COLUMN kind TEXT")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS subscriptions (
                    node_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    place TEXT NOT NULL DEFAULT 'both',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (node_id, kind)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS node_labels (
                    node_id TEXT PRIMARY KEY,
                    alias TEXT,
                    note TEXT,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    user_label TEXT,
                    user_label_at REAL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    channel TEXT NOT NULL,
                    channel_index INTEGER,
                    direction TEXT NOT NULL,
                    node_id TEXT,
                    text TEXT NOT NULL
                )
                """
            )
            ch_cols = {
                r["name"]
                for r in conn.execute("PRAGMA table_info(channel_log)").fetchall()
            }
            if "ack_status" not in ch_cols:
                conn.execute("ALTER TABLE channel_log ADD COLUMN ack_status TEXT")


    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO settings(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def delete_setting(self, key: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))

    def get_dedupe_keys(self, kind: str, day: str) -> set[str]:
        import json

        raw = self.get_setting(f"dedupe:{kind}:{day}") or "[]"
        try:
            return set(json.loads(raw))
        except Exception:  # noqa: BLE001
            return set()

    def save_dedupe_keys(self, kind: str, day: str, keys: set[str]) -> None:
        import json

        self.set_setting(f"dedupe:{kind}:{day}", json.dumps(sorted(keys)))

    def get_alert_poll_ts(self) -> float:
        raw = self.get_setting("dedupe:alert_poll_ts")
        try:
            return float(raw or 0)
        except (TypeError, ValueError):
            return 0.0

    def set_alert_poll_ts(self, ts: float) -> None:
        self.set_setting("dedupe:alert_poll_ts", str(ts))

    def playa_hotfix_dm_sent(self) -> set[str]:
        from bot.version import playa_hotfix_dm_setting_key

        raw = self.get_setting(playa_hotfix_dm_setting_key()) or ""
        return {x.strip() for x in raw.split(",") if x.strip()}

    def mark_playa_hotfix_dm(self, node_ids: list[str]) -> None:
        from bot.version import playa_hotfix_dm_setting_key

        sent = self.playa_hotfix_dm_sent()
        sent.update(nid.strip() for nid in node_ids if nid and nid.strip())
        self.set_setting(playa_hotfix_dm_setting_key(), ",".join(sorted(sent)))

    def unlock_user(self, node_id: str, short_name: str | None, is_admin: bool) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO users(node_id, short_name, unlocked_at, last_seen, is_admin)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    short_name = COALESCE(excluded.short_name, users.short_name),
                    last_seen = excluded.last_seen,
                    is_admin = MAX(users.is_admin, excluded.is_admin)
                """,
                (node_id, short_name, now, now, 1 if is_admin else 0),
            )

    def touch_user(self, node_id: str, short_name: str | None = None) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE users
                SET last_seen = ?, short_name = COALESCE(?, short_name)
                WHERE node_id = ?
                """,
                (now, short_name, node_id),
            )

    def is_unlocked(self, node_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM users WHERE node_id = ?", (node_id,)
            ).fetchone()
            return row is not None

    def revoke_user(self, node_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM users WHERE node_id = ?", (node_id,))

    def revoke_user_full(self, node_id: str) -> None:
        """Drop unlock row plus alert subs and labels (stale / duplicate cleanup)."""
        with self._conn() as conn:
            conn.execute("DELETE FROM users WHERE node_id = ?", (node_id,))
            conn.execute("DELETE FROM subscriptions WHERE node_id = ?", (node_id,))
            conn.execute("DELETE FROM node_labels WHERE node_id = ?", (node_id,))

    def user_has_interacted(self, node_id: str) -> bool:
        """True if they set iam or sent any logged query/DM prompt."""
        if self.has_user_identity(node_id):
            return True
        with self._conn() as conn:
            if conn.execute(
                "SELECT 1 FROM queries WHERE node_id = ? LIMIT 1", (node_id,)
            ).fetchone():
                return True
            return bool(
                conn.execute(
                    """
                    SELECT 1 FROM channel_log
                    WHERE node_id = ? AND direction = 'rx' AND channel = 'DM'
                    LIMIT 1
                    """,
                    (node_id,),
                ).fetchone()
            )

    def list_interacted_user_ids(self) -> list[str]:
        return [
            u["node_id"]
            for u in self.list_users()
            if self.user_has_interacted(u["node_id"])
        ]

    def audit_users(
        self,
        mesh_ids: set[str],
        *,
        stale_seconds: float = 7 * 86400,
    ) -> dict[str, Any]:
        """Flag stale unlocks and duplicate iam names (keep newest / on-mesh)."""
        labels = self.labels_map()
        users_by_id = {u["node_id"]: u for u in self.list_users()}
        now = time.time()

        by_label: dict[str, list[str]] = defaultdict(list)
        for nid, lab in labels.items():
            if nid not in users_by_id:
                continue
            name = (lab.get("user_label") or "").strip()
            if len(name) >= 2:
                by_label[name.casefold()].append(nid)

        duplicates: list[dict[str, Any]] = []
        duplicate_revoke: set[str] = set()
        for _key, nids in by_label.items():
            if len(nids) < 2:
                continue

            def rank(nid: str) -> tuple[int, float]:
                in_mesh = 1 if nid in mesh_ids else 0
                last = float((users_by_id.get(nid) or {}).get("last_seen") or 0)
                return (in_mesh, last)

            ranked = sorted(nids, key=rank, reverse=True)
            keep = ranked[0]
            revoke = ranked[1:]
            duplicate_revoke.update(revoke)
            duplicates.append(
                {
                    "label": (labels.get(keep) or {}).get("user_label") or keep,
                    "keep": keep,
                    "revoke": revoke,
                }
            )

        stale: list[dict[str, Any]] = []
        for u in users_by_id.values():
            nid = u["node_id"]
            if nid in duplicate_revoke:
                continue
            if nid in mesh_ids:
                continue
            last = float(u.get("last_seen") or 0)
            if now - last <= stale_seconds:
                continue
            stale.append(
                {
                    "node_id": nid,
                    "last_seen": last,
                    "reason": f"not on mesh · last seen {_ago_admin(last)}",
                }
            )

        flagged_revoke = sorted(duplicate_revoke | {s["node_id"] for s in stale})
        return {
            "duplicates": duplicates,
            "stale": stale,
            "flagged_revoke": flagged_revoke,
        }

    def apply_playa_alert_defaults(self, node_id: str) -> None:
        self.set_subscription(node_id, "sun", enabled=True, place="brc")
        self.set_subscription(node_id, "alert", enabled=True, place="brc")

    def list_users(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM users ORDER BY last_seen DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def log_query(
        self,
        *,
        node_id: str,
        short_name: str | None,
        prompt: str,
        reply: str | None,
        mode: str,
        latency_ms: int | None,
        error: str | None = None,
        kind: str = "chat",
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO queries(ts, node_id, short_name, prompt, reply, mode, latency_ms, error, kind)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    time.time(),
                    node_id,
                    short_name,
                    prompt,
                    reply,
                    mode,
                    latency_ms,
                    error,
                    kind,
                ),
            )

    def list_queries(self, limit: int = 100, kind: str | None = None) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if kind:
                rows = conn.execute(
                    """
                    SELECT * FROM queries
                    WHERE kind = ?
                      AND IFNULL(mode,'') != 'push'
                      AND node_id != 'admin'
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (kind, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM queries
                    WHERE IFNULL(mode,'') != 'push'
                      AND node_id != 'admin'
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def log_push(
        self,
        *,
        action: str,
        target: str | None = None,
        node_id: str | None = None,
        channel: str | None = None,
        detail: str | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO pushes(ts, action, target, node_id, channel, detail)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    time.time(),
                    action,
                    target,
                    node_id,
                    channel,
                    (detail or "")[:2000],
                ),
            )
            conn.execute(
                """
                DELETE FROM pushes WHERE id NOT IN (
                    SELECT id FROM pushes ORDER BY id DESC LIMIT 2000
                )
                """
            )

    def list_pushes(self, limit: int = 300) -> list[dict[str, Any]]:
        with self._conn() as conn:
            fresh = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM pushes ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            ]
            leftover = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT id, ts, mode AS action, short_name AS target, node_id,
                           NULL AS channel, COALESCE(prompt, reply, '') AS detail
                    FROM queries
                    WHERE mode = 'push' OR node_id = 'admin'
                    ORDER BY id DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            ]
        # Prefer dedicated pushes; fill remaining from old query rows
        seen = {(p.get("ts"), p.get("detail"), p.get("node_id")) for p in fresh}
        extra = []
        for r in leftover:
            key = (r.get("ts"), r.get("detail"), r.get("node_id"))
            if key in seen:
                continue
            extra.append(r)
        combined = fresh + extra
        combined.sort(key=lambda x: float(x.get("ts") or 0), reverse=True)
        return combined[:limit]

    def list_chords(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.list_queries(limit=limit, kind="chords")

    def set_subscription(
        self, node_id: str, kind: str, *, enabled: bool, place: str = "both"
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO subscriptions(node_id, kind, place, enabled, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(node_id, kind) DO UPDATE SET
                    place = excluded.place,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (node_id, kind, place, 1 if enabled else 0, time.time()),
            )

    def bulk_set_subscription_place(
        self, place: str, *, kinds: tuple[str, ...] = ("sun", "alert")
    ) -> int:
        if place not in ("reno", "brc", "both"):
            place = "reno"
        now = time.time()
        with self._conn() as conn:
            placeholders = ",".join("?" * len(kinds))
            cur = conn.execute(
                f"""
                UPDATE subscriptions
                SET place = ?, updated_at = ?
                WHERE kind IN ({placeholders})
                """,
                (place, now, *kinds),
            )
            return cur.rowcount

    def migrate_subscription_places_to_brc(self) -> list[tuple[str, str]]:
        """Move reno/both rows to brc. Returns enabled (node_id, kind) pairs that changed."""
        now = time.time()
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT node_id, kind, enabled, place
                FROM subscriptions
                WHERE place IN ('reno', 'both')
                """
            ).fetchall()
            if not rows:
                return []
            enabled_changed = [
                (str(r["node_id"]), str(r["kind"]))
                for r in rows
                if int(r["enabled"] or 0)
            ]
            conn.execute(
                """
                UPDATE subscriptions
                SET place = 'brc', updated_at = ?
                WHERE place IN ('reno', 'both')
                """,
                (now,),
            )
            return enabled_changed

    def list_subscriptions(self, kind: str, *, enabled_only: bool = True) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if enabled_only:
                rows = conn.execute(
                    """
                    SELECT * FROM subscriptions
                    WHERE kind = ? AND enabled = 1
                    """,
                    (kind,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM subscriptions WHERE kind = ?",
                    (kind,),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_label(self, node_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM node_labels WHERE node_id = ?", (node_id,)
            ).fetchone()
            return dict(row) if row else None

    def labels_map(self) -> dict[str, dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM node_labels").fetchall()
            return {r["node_id"]: dict(r) for r in rows}

    def set_admin_label(
        self,
        node_id: str,
        *,
        alias: str | None = None,
        note: str | None = None,
        favorite: bool | None = None,
    ) -> None:
        """Upsert admin-controlled fields. Pass None to leave a field unchanged;
        pass '' to clear alias/note."""
        now = time.time()
        existing = self.get_label(node_id) or {}
        new_alias = existing.get("alias") if alias is None else (alias.strip() or None)
        new_note = existing.get("note") if note is None else (note.strip() or None)
        new_fav = (
            int(existing.get("favorite") or 0)
            if favorite is None
            else (1 if favorite else 0)
        )
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO node_labels(
                    node_id, alias, note, favorite, user_label, user_label_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    alias = excluded.alias,
                    note = excluded.note,
                    favorite = excluded.favorite,
                    updated_at = excluded.updated_at
                """,
                (
                    node_id,
                    new_alias,
                    new_note,
                    new_fav,
                    existing.get("user_label"),
                    existing.get("user_label_at"),
                    now,
                ),
            )

    def set_user_label(self, node_id: str, label: str) -> str:
        """User self-ID. Returns cleaned label. Admin alias still wins for display."""
        cleaned = " ".join(label.strip().split())[:32]
        if len(cleaned) < 2:
            raise ValueError("empty label")
        # Reject MAC-nibble / pure numeric stand-ins — want a real name
        if Store._cryptic_short(cleaned, node_id) and len(cleaned) <= 4:
            raise ValueError("cryptic")
        if cleaned.isdigit():
            raise ValueError("numeric")
        now = time.time()
        existing = self.get_label(node_id) or {}
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO node_labels(
                    node_id, alias, note, favorite, user_label, user_label_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    user_label = excluded.user_label,
                    user_label_at = excluded.user_label_at,
                    updated_at = excluded.updated_at
                """,
                (
                    node_id,
                    existing.get("alias"),
                    existing.get("note"),
                    int(existing.get("favorite") or 0),
                    cleaned,
                    now,
                    now,
                ),
            )
        return cleaned

    def has_user_identity(self, node_id: str) -> bool:
        lab = self.get_label(node_id)
        return bool(lab and (lab.get("user_label") or "").strip())

    def clear_user_label(self, node_id: str) -> None:
        existing = self.get_label(node_id)
        if not existing:
            return
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE node_labels
                SET user_label = NULL, user_label_at = NULL, updated_at = ?
                WHERE node_id = ?
                """,
                (time.time(), node_id),
            )

    @staticmethod
    def _cryptic_short(short: str | None, node_id: str | None) -> bool:
        """True when short name is just a MAC nibble (e.g. 5e84), not a real nickname."""
        s = (short or "").strip()
        if not s:
            return True
        if len(s) == 4 and all(c in "0123456789abcdefABCDEF" for c in s):
            return True
        if node_id:
            nid = str(node_id).lstrip("!").lower()
            if s.lower() == nid[-4:]:
                return True
        return False

    @staticmethod
    def display_name(
        label: dict[str, Any] | None,
        *,
        mesh_short: str | None = None,
        mesh_long: str | None = None,
        node_id: str | None = None,
    ) -> str:
        """Admin alias > user iam > mesh long > mesh short > node id.

        Prefers long name over cryptic 4-hex short names that are just MAC fragments.
        """
        if label:
            if label.get("alias"):
                return str(label["alias"])
            if label.get("user_label"):
                return str(label["user_label"])
        short = (mesh_short or "").strip() or None
        long = (mesh_long or "").strip() or None
        if long and Store._cryptic_short(short, node_id):
            return long
        if short and not Store._cryptic_short(short, node_id):
            return short
        return long or short or node_id or "?"

    def resolve_label_token(self, token: str) -> tuple[str | None, str | None]:
        """Match admin alias or user_label (exact, case-insensitive)."""
        low = token.strip().lower()
        if not low:
            return None, None
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM node_labels").fetchall()
        exact: list[dict[str, Any]] = []
        partial: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            for field in ("alias", "user_label"):
                val = (d.get(field) or "").strip()
                if not val:
                    continue
                if val.lower() == low:
                    exact.append(d)
                elif low in val.lower():
                    partial.append(d)
        if len(exact) == 1:
            d = exact[0]
            return d["node_id"], self.display_name(d, node_id=d["node_id"])
        if not exact and len(partial) == 1:
            d = partial[0]
            return d["node_id"], self.display_name(d, node_id=d["node_id"])
        return None, None

    def log_channel_message(
        self,
        *,
        channel: str,
        channel_index: int | None,
        direction: str,
        node_id: str | None,
        text: str,
        ack_status: str | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO channel_log(
                    ts, channel, channel_index, direction, node_id, text, ack_status
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    time.time(),
                    channel,
                    channel_index,
                    direction,
                    node_id,
                    (text or "")[:2000],
                    ack_status,
                ),
            )
            # Keep last ~2000 rows
            conn.execute(
                """
                DELETE FROM channel_log WHERE id NOT IN (
                    SELECT id FROM channel_log ORDER BY id DESC LIMIT 2000
                )
                """
            )

    def list_channel_log(
        self, *, channel: str | None = "MetalDetect", limit: int = 200
    ) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if channel:
                rows = conn.execute(
                    """
                    SELECT * FROM channel_log
                    WHERE lower(channel) = lower(?)
                    ORDER BY id DESC LIMIT ?
                    """,
                    (channel, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM channel_log ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict[str, int]:
        with self._conn() as conn:
            users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
            queries = conn.execute("SELECT COUNT(*) AS c FROM queries").fetchone()["c"]
            chords = conn.execute(
                "SELECT COUNT(*) AS c FROM queries WHERE kind = 'chords'"
            ).fetchone()["c"]
            sun = conn.execute(
                "SELECT COUNT(*) AS c FROM subscriptions WHERE kind = 'sun' AND enabled = 1"
            ).fetchone()["c"]
            alert = conn.execute(
                "SELECT COUNT(*) AS c FROM subscriptions WHERE kind = 'alert' AND enabled = 1"
            ).fetchone()["c"]
            return {
                "users": users,
                "queries": queries,
                "chords": chords,
                "sun": sun,
                "alert": alert,
            }


def _ago_admin(ts: float | None) -> str:
    if not ts:
        return "never"
    sec = max(0, int(time.time() - float(ts)))
    if sec < 3600:
        return f"{sec // 60}m ago"
    if sec < 86400:
        return f"{sec // 3600}h ago"
    return f"{sec // 86400}d ago"
