"""Email (SMTP) with a file fallback. Config from env; nothing is hard-coded.

SMTP_HOST, SMTP_PORT (465 = SSL), SMTP_USER, SMTP_PASS, ALERT_TO.
If unset, the message is written to data/outbox/ so nothing is silently dropped.
"""
from __future__ import annotations

import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

OUTBOX = Path(__file__).parent / "data" / "outbox"


def send(subject: str, body: str) -> bool:
    host, user, pw, to = (os.getenv(k, "") for k in
                          ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "ALERT_TO"))
    if not (host and user and pw and to):
        OUTBOX.mkdir(parents=True, exist_ok=True)
        f = OUTBOX / f"{datetime.now():%Y%m%d_%H%M%S}.txt"
        f.write_text(f"{subject}\n\n{body}", encoding="utf-8")
        print(f"  [WARN] SMTP not configured; saved to {f}", flush=True)
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL(host, int(os.getenv("SMTP_PORT", "465")), timeout=20) as s:
            s.login(user, pw)
            s.send_message(msg)
        return True
    except Exception as e:
        print(f"  [WARN] email failed: {e}", flush=True)
        OUTBOX.mkdir(parents=True, exist_ok=True)
        (OUTBOX / f"{datetime.now():%Y%m%d_%H%M%S}_failed.txt").write_text(
            f"{subject}\n\n{body}", encoding="utf-8")
        return False
