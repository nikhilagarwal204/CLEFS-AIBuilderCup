"""
ask_logs, ask_saved_views and ask_wa_optins. Every read is scoped by hotel
AND user: an owner's questions and views are theirs, never a colleague's.
"""

from __future__ import annotations

from typing import Any

from clefs import db
from clefs.core import clock
from clefs.core.errors import ClefsError

# ─── Logs ───

_LOG_COLS = ("id, hotel_slug, user_id, channel, question, tool_calls, answer, answer_text, status, unsupported, "
             "reason, grounded, regenerated, saved_view_id, latency_ms, tokens, feedback, feedback_note, "
             "feedback_at, created_at")


def insert_log(cur: Any, hotel_slug: str, *, user_id: int | None, channel: str, question: str, tool_calls: list,
               answer: dict, status: str, reason: str | None, grounded: bool | None, regenerated: bool,
               latency_ms: int | None, tokens: int | None, saved_view_id: int | None = None) -> int:
    cur.execute(
        """
        INSERT INTO ask_logs (hotel_slug, user_id, channel, question, tool_calls, answer, answer_text, status,
                              unsupported, reason, grounded, regenerated, saved_view_id, latency_ms, tokens, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (hotel_slug, user_id, channel, question[:2000], db.Json(tool_calls), db.Json(answer),
         (answer.get("answer_text") or "")[:4000], status, status == "unsupported", reason, grounded, regenerated,
         saved_view_id, latency_ms, tokens, clock.now()),
    )
    return int(cur.fetchone()["id"])


def log_to_api(r: dict) -> dict:
    return {
        "id": r["id"], "channel": r["channel"], "question": r["question"], "status": r["status"],
        "unsupported": r["unsupported"], "reason": r["reason"], "grounded": r["grounded"],
        "regenerated": r["regenerated"], "answer": r["answer"], "tool_calls": r["tool_calls"],
        "saved_view_id": r["saved_view_id"], "latency_ms": r["latency_ms"], "tokens": r["tokens"],
        "feedback": r["feedback"], "feedback_note": r["feedback_note"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
    }


def get_log(cur: Any, hotel_slug: str, log_id: int) -> dict | None:
    cur.execute(f"SELECT {_LOG_COLS} FROM ask_logs WHERE hotel_slug = %s AND id = %s", (hotel_slug, log_id))
    row = cur.fetchone()
    return dict(row) if row else None


def list_logs(cur: Any, hotel_slug: str, *, user_id: int | None, limit: int = 20, all_users: bool = False,
              feedback: int | None = None) -> list[dict]:
    where, params = ["hotel_slug = %s"], [hotel_slug]
    if not all_users:
        where.append("user_id = %s")
        params.append(user_id)
    if feedback is not None:
        where.append("feedback = %s")
        params.append(feedback)
    cur.execute(f"SELECT {_LOG_COLS} FROM ask_logs WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT %s",
                (*params, max(1, min(100, limit))))
    return [dict(r) for r in cur.fetchall()]


def set_feedback(cur: Any, hotel_slug: str, log_id: int, *, user_id: int, value: int, note: str | None) -> dict:
    cur.execute(
        """
        UPDATE ask_logs SET feedback = %s, feedback_note = %s, feedback_at = %s
        WHERE hotel_slug = %s AND id = %s AND user_id = %s
        RETURNING """ + _LOG_COLS,
        (value, (note or "").strip()[:1000] or None, clock.now(), hotel_slug, log_id, user_id),
    )
    row = cur.fetchone()
    if row is None:
        raise ClefsError(404, "Answer not found", "ask_log_not_found")
    return dict(row)


def tokens_since(cur: Any, hotel_slug: str, after_id: int) -> int | None:
    """Tokens of this hotel's ask.* AI calls logged after `after_id`
    (clefs.ai.tool_call returns calls, not usage; ai_calls has both)."""
    cur.execute(
        "SELECT SUM(COALESCE(tokens_in, 0) + COALESCE(tokens_out, 0)) AS n, COUNT(*) AS c FROM ai_calls "
        "WHERE id > %s AND hotel_slug = %s AND task LIKE 'ask.%%'",
        (after_id, hotel_slug),
    )
    row = cur.fetchone()
    return int(row["n"]) if row and row["c"] else None


def last_ai_call_id(cur: Any) -> int:
    cur.execute("SELECT COALESCE(MAX(id), 0) AS id FROM ai_calls")
    return int(cur.fetchone()["id"])


# ─── Saved views ───

_VIEW_COLS = "id, hotel_slug, user_id, title, query, question, source_log_id, pinned, last_run_at, created_at, updated_at"


def view_to_api(r: dict) -> dict:
    return {
        "id": r["id"], "title": r["title"], "query": r["query"], "question": r["question"],
        "source_log_id": r["source_log_id"], "pinned": r["pinned"],
        "last_run_at": r["last_run_at"].isoformat() if r["last_run_at"] else None,
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
    }


def list_views(cur: Any, hotel_slug: str, user_id: int) -> list[dict]:
    cur.execute(f"SELECT {_VIEW_COLS} FROM ask_saved_views WHERE hotel_slug = %s AND user_id = %s "
                "ORDER BY pinned DESC, created_at DESC, id DESC", (hotel_slug, user_id))
    return [dict(r) for r in cur.fetchall()]


def get_view(cur: Any, hotel_slug: str, user_id: int, view_id: int) -> dict:
    cur.execute(f"SELECT {_VIEW_COLS} FROM ask_saved_views WHERE hotel_slug = %s AND user_id = %s AND id = %s",
                (hotel_slug, user_id, view_id))
    row = cur.fetchone()
    if row is None:
        raise ClefsError(404, "Saved view not found", "ask_view_not_found")
    return dict(row)


def create_view(cur: Any, hotel_slug: str, user_id: int, *, title: str, query: dict, question: str | None,
                source_log_id: int | None, pinned: bool) -> dict:
    now = clock.now()
    cur.execute(
        f"""
        INSERT INTO ask_saved_views (hotel_slug, user_id, title, query, question, source_log_id, pinned,
                                     created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_VIEW_COLS}
        """,
        (hotel_slug, user_id, title, db.Json(query), question, source_log_id, pinned, now, now),
    )
    return dict(cur.fetchone())


def update_view(cur: Any, hotel_slug: str, user_id: int, view_id: int, *, title: str | None,
                pinned: bool | None) -> dict:
    get_view(cur, hotel_slug, user_id, view_id)
    cur.execute(
        f"""
        UPDATE ask_saved_views SET title = COALESCE(%s, title), pinned = COALESCE(%s, pinned), updated_at = %s
        WHERE hotel_slug = %s AND user_id = %s AND id = %s RETURNING {_VIEW_COLS}
        """,
        (title, pinned, clock.now(), hotel_slug, user_id, view_id),
    )
    return dict(cur.fetchone())


def delete_view(cur: Any, hotel_slug: str, user_id: int, view_id: int) -> None:
    cur.execute("DELETE FROM ask_saved_views WHERE hotel_slug = %s AND user_id = %s AND id = %s",
                (hotel_slug, user_id, view_id))
    if cur.rowcount == 0:
        raise ClefsError(404, "Saved view not found", "ask_view_not_found")


def touch_view(cur: Any, view_id: int) -> None:
    cur.execute("UPDATE ask_saved_views SET last_run_at = %s WHERE id = %s", (clock.now(), view_id))


# ─── WhatsApp opt-in ───


def get_optin(cur: Any, hotel_slug: str, user_id: int) -> dict | None:
    cur.execute("SELECT hotel_slug, user_id, opted_in, method, last_asked_at, updated_at FROM ask_wa_optins "
                "WHERE hotel_slug = %s AND user_id = %s", (hotel_slug, user_id))
    row = cur.fetchone()
    return dict(row) if row else None


def set_optin(cur: Any, hotel_slug: str, user_id: int, *, opted_in: bool, method: str) -> dict:
    cur.execute(
        """
        INSERT INTO ask_wa_optins (hotel_slug, user_id, opted_in, method, updated_at) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (hotel_slug, user_id) DO UPDATE SET opted_in = EXCLUDED.opted_in, method = EXCLUDED.method,
            updated_at = EXCLUDED.updated_at
        RETURNING hotel_slug, user_id, opted_in, method, last_asked_at, updated_at
        """,
        (hotel_slug, user_id, opted_in, method, clock.now()),
    )
    return dict(cur.fetchone())


def optins_for_user(cur: Any, user_id: int) -> list[dict]:
    """Hotels where this user opted in AND still holds an owner/gm/finance
    membership, most recently asked first."""
    cur.execute(
        """
        SELECT o.hotel_slug, o.last_asked_at, m.role FROM ask_wa_optins o
        JOIN hotel_memberships m ON m.user_id = o.user_id AND m.hotel_slug = o.hotel_slug AND m.is_active
        WHERE o.user_id = %s AND o.opted_in AND m.role IN ('owner', 'gm', 'finance')
        ORDER BY o.last_asked_at DESC NULLS LAST, o.hotel_slug
        """,
        (user_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def mark_asked(cur: Any, hotel_slug: str, user_id: int) -> None:
    cur.execute("UPDATE ask_wa_optins SET last_asked_at = %s WHERE hotel_slug = %s AND user_id = %s",
                (clock.now(), hotel_slug, user_id))
