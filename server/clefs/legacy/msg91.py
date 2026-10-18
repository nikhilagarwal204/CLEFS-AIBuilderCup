"""
Legacy MSG91 SMS alert to the staff member a request was auto-assigned to.

Moved verbatim in behaviour from the monolith so the Indian pilots keep getting
texts. It is fire-and-forget on a daemon thread: a failed SMS must never break
request creation. MSG91 cannot deliver in the UAE; the notify package's
twilio path covers UAE hotels.

MSG91 template variables (registered in DLT):
  ##room## room number, ##task## request type, ##desc## short description,
  ##priority## HIGH / NORMAL / LOW
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from typing import Any

from clefs.settings import get_settings

log = logging.getLogger("clefs.sms.msg91")

SMS_FLOW_URL = "https://control.msg91.com/api/v5/flow"


def sms_enabled() -> bool:
    s = get_settings()
    return bool(s.SMS_ENABLED and s.SMS_AUTH_KEY and s.SMS_TEMPLATE_ID)


def normalize_indian_mobile(phone_number: str) -> str:
    """Ensure a 91 prefix, no +, no spaces or dashes (what MSG91 expects)."""
    clean = phone_number.strip().replace(" ", "").replace("-", "")
    if clean.startswith("+"):
        clean = clean[1:]
    if not clean.startswith("91"):
        clean = "91" + clean
    return clean


def build_payload(phone_number: str, room: str, task_type: str, description: str, priority: str) -> dict[str, Any]:
    short_desc = (description or "Service request")[:120]
    priority_label = {"high": "HIGH", "medium": "NORMAL", "low": "LOW"}.get(priority, "NORMAL")
    return {
        "template_id": get_settings().SMS_TEMPLATE_ID,
        "recipients": [
            {
                "mobiles": normalize_indian_mobile(phone_number),
                "room": room or "-",
                "task": task_type or "Service Request",
                "desc": short_desc,
                "priority": priority_label,
            }
        ],
    }


def send_sms_alert(phone_number: str, room: str, task_type: str, description: str, priority: str) -> None:
    if not sms_enabled() or not phone_number:
        return
    payload = json.dumps(build_payload(phone_number, room, task_type, description, priority)).encode("utf-8")
    auth_key = get_settings().SMS_AUTH_KEY or ""

    def _fire() -> None:
        try:
            req = urllib.request.Request(
                SMS_FLOW_URL,
                data=payload,
                headers={"authkey": auth_key, "Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                log.info("[SMS] sent: %s", resp.status)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.warning("[SMS] failed: %s", exc)

    threading.Thread(target=_fire, daemon=True).start()
