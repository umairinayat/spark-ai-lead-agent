"""Persistence layer.

SQLite through the standard library rather than an ORM. The data model is four
flat tables and the whole file is ~200 lines; adding SQLAlchemy would add a
dependency, a migration story and an extra abstraction to explain, and buy
nothing at this size. Swapping to Postgres means changing this file only -
every caller goes through `LeadRepository`.

`agent_runs` stores the tool-call trace for each classification. That is what
lets you answer "why did the agent call this lead High?" three weeks later.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id                TEXT PRIMARY KEY,
    received_at       TEXT NOT NULL,
    name              TEXT NOT NULL,
    email             TEXT NOT NULL,
    company           TEXT,
    message           TEXT NOT NULL,
    source            TEXT,
    priority          TEXT,
    intent            TEXT,
    confidence        REAL,
    reason            TEXT,
    summary           TEXT,
    follow_up_message TEXT,
    signals           TEXT,
    recommended_owner TEXT,
    crm_provider      TEXT,
    crm_contact_id    TEXT,
    crm_opportunity_id TEXT,
    crm_stage         TEXT,
    notified          INTEGER DEFAULT 0,
    degraded          INTEGER DEFAULT 0,
    pipeline_source   TEXT
);
CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);
CREATE INDEX IF NOT EXISTS idx_leads_priority ON leads(priority);

CREATE TABLE IF NOT EXISTS agent_runs (
    id          TEXT PRIMARY KEY,
    lead_id     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    model       TEXT,
    degraded    INTEGER DEFAULT 0,
    degraded_reason TEXT,
    attempts    INTEGER DEFAULT 1,
    latency_ms  INTEGER DEFAULT 0,
    tool_calls  TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_lead ON agent_runs(lead_id);

CREATE TABLE IF NOT EXISTS mock_crm_contacts (
    id          TEXT PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    name        TEXT,
    company     TEXT,
    phone       TEXT,
    tags        TEXT,
    pipeline    TEXT,
    stage       TEXT,
    custom_fields TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    touch_count INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS notifications (
    id          TEXT PRIMARY KEY,
    lead_id     TEXT,
    created_at  TEXT NOT NULL,
    channel     TEXT,
    sent        INTEGER DEFAULT 0,
    payload     TEXT,
    error       TEXT
);
"""


class LeadRepository:
    """Thread-safe SQLite repository."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    # ------------------------------------------------------------ internals
    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._conn() as conn:
            conn.executescript(SCHEMA)

    # ---------------------------------------------------------------- leads
    def save_lead(self, record: dict[str, Any]) -> None:
        columns = [
            "id", "received_at", "name", "email", "company", "message", "source",
            "priority", "intent", "confidence", "reason", "summary",
            "follow_up_message", "signals", "recommended_owner", "crm_provider",
            "crm_contact_id", "crm_opportunity_id", "crm_stage", "notified",
            "degraded", "pipeline_source",
        ]
        values = [record.get(c) for c in columns]
        placeholders = ",".join("?" * len(columns))
        with self._lock, self._conn() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO leads ({','.join(columns)}) "
                f"VALUES ({placeholders})",
                values,
            )

    def get_lead(self, lead_id: str) -> Optional[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return _row_to_lead(row) if row else None

    def list_leads(
        self, limit: int = 50, offset: int = 0, priority: str = ""
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM leads"
        params: list[Any] = []
        if priority:
            sql += " WHERE priority = ?"
            params.append(priority)
        sql += " ORDER BY received_at DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        with self._lock, self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_lead(r) for r in rows]

    def history_for_email(self, email: str) -> dict[str, Any]:
        """Evidence for the `lookup_existing_contact` tool."""
        email = (email or "").strip().lower()
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT priority, received_at, company FROM leads "
                "WHERE lower(email) = ? ORDER BY received_at DESC LIMIT 5",
                (email,),
            ).fetchall()
            crm = conn.execute(
                "SELECT id, stage, touch_count FROM mock_crm_contacts "
                "WHERE lower(email) = ?",
                (email,),
            ).fetchone()
        return {
            "known": bool(rows or crm),
            "times_seen": len(rows),
            "previous_priorities": [r["priority"] for r in rows if r["priority"]],
            "last_seen": rows[0]["received_at"] if rows else "",
            "in_crm": bool(crm),
            "crm_contact_id": crm["id"] if crm else "",
            "crm_stage": crm["stage"] if crm else "",
        }

    def stats(self) -> dict[str, Any]:
        with self._lock, self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"]
            by_priority = {
                r["priority"] or "unknown": r["c"]
                for r in conn.execute(
                    "SELECT priority, COUNT(*) c FROM leads GROUP BY priority"
                ).fetchall()
            }
            by_intent = {
                r["intent"] or "unknown": r["c"]
                for r in conn.execute(
                    "SELECT intent, COUNT(*) c FROM leads GROUP BY intent"
                ).fetchall()
            }
            notified = conn.execute(
                "SELECT COUNT(*) c FROM leads WHERE notified = 1"
            ).fetchone()["c"]
            degraded = conn.execute(
                "SELECT COUNT(*) c FROM leads WHERE degraded = 1"
            ).fetchone()["c"]
        return {
            "total_leads": total,
            "by_priority": by_priority,
            "by_intent": by_intent,
            "notifications_sent": notified,
            "degraded_classifications": degraded,
        }

    # ----------------------------------------------------------- agent runs
    def save_agent_run(self, record: dict[str, Any]) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO agent_runs "
                "(id, lead_id, created_at, model, degraded, degraded_reason, "
                " attempts, latency_ms, tool_calls) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    record["id"], record["lead_id"], record["created_at"],
                    record.get("model"), int(record.get("degraded", 0)),
                    record.get("degraded_reason", ""), record.get("attempts", 1),
                    record.get("latency_ms", 0),
                    json.dumps(record.get("tool_calls", []), default=str),
                ),
            )

    def agent_runs_for(self, lead_id: str) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_runs WHERE lead_id = ? ORDER BY created_at",
                (lead_id,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["tool_calls"] = json.loads(d.get("tool_calls") or "[]")
            d["degraded"] = bool(d["degraded"])
            out.append(d)
        return out

    # ------------------------------------------------------------- mock crm
    def upsert_mock_contact(self, contact: dict[str, Any]) -> tuple[str, bool]:
        email = contact["email"].strip().lower()
        with self._lock, self._conn() as conn:
            existing = conn.execute(
                "SELECT id, touch_count FROM mock_crm_contacts WHERE lower(email) = ?",
                (email,),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE mock_crm_contacts SET name=?, company=?, phone=?, "
                    "tags=?, pipeline=?, stage=?, custom_fields=?, updated_at=?, "
                    "touch_count=? WHERE id=?",
                    (
                        contact.get("name"), contact.get("company"),
                        contact.get("phone"), json.dumps(contact.get("tags", [])),
                        contact.get("pipeline"), contact.get("stage"),
                        json.dumps(contact.get("custom_fields", {})),
                        contact["updated_at"], existing["touch_count"] + 1,
                        existing["id"],
                    ),
                )
                return existing["id"], False
            conn.execute(
                "INSERT INTO mock_crm_contacts (id, email, name, company, phone, "
                "tags, pipeline, stage, custom_fields, created_at, updated_at, "
                "touch_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,1)",
                (
                    contact["id"], email, contact.get("name"),
                    contact.get("company"), contact.get("phone"),
                    json.dumps(contact.get("tags", [])), contact.get("pipeline"),
                    contact.get("stage"),
                    json.dumps(contact.get("custom_fields", {})),
                    contact["created_at"], contact["updated_at"],
                ),
            )
            return contact["id"], True

    def list_mock_contacts(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM mock_crm_contacts ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d.get("tags") or "[]")
            d["custom_fields"] = json.loads(d.get("custom_fields") or "{}")
            out.append(d)
        return out

    # -------------------------------------------------------- notifications
    def save_notification(self, record: dict[str, Any]) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO notifications "
                "(id, lead_id, created_at, channel, sent, payload, error) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    record["id"], record.get("lead_id"), record["created_at"],
                    record.get("channel"), int(record.get("sent", 0)),
                    json.dumps(record.get("payload", {}), default=str),
                    record.get("error", ""),
                ),
            )

    def list_notifications(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d.get("payload") or "{}")
            d["sent"] = bool(d["sent"])
            out.append(d)
        return out


def _row_to_lead(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["signals"] = json.loads(d.get("signals") or "[]")
    d["notified"] = bool(d.get("notified"))
    d["degraded"] = bool(d.get("degraded"))
    return d
