"""A Django email backend that sends via the Mailjet Send API (v3.1) over HTTPS.

Uses only the standard library (no extra dependency). Configure with:
    MAIL_BACKEND=mailjet
    MAILJET_API_KEY / MAILJET_API_SECRET
    MAIL_FROM_ADDRESS / MAIL_FROM_NAME  (default From)
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

log = logging.getLogger("flexee")

MAILJET_URL = "https://api.mailjet.com/v3.1/send"


class EmailBackend(BaseEmailBackend):
    def __init__(self, fail_silently: bool = False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.api_key = getattr(settings, "MAILJET_API_KEY", "")
        self.api_secret = getattr(settings, "MAILJET_API_SECRET", "")
        self.from_email = getattr(settings, "MAIL_FROM_ADDRESS", "")
        self.from_name = getattr(settings, "MAIL_FROM_NAME", "")

    def _auth_header(self) -> str:
        raw = f"{self.api_key}:{self.api_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def _message_to_payload(self, msg) -> dict | None:
        recipients = [{"Email": a} for a in msg.to if a]
        if not recipients:
            return None
        # Parse "Name <email>" senders; fall back to configured From.
        from_email, from_name = self.from_email, self.from_name
        sender = msg.from_email or ""
        if "<" in sender and ">" in sender:
            from_name = sender.split("<")[0].strip().strip('"') or from_name
            from_email = sender.split("<")[1].split(">")[0].strip()
        elif sender:
            from_email = sender

        payload = {
            "From": {"Email": from_email, "Name": from_name or from_email},
            "To": recipients,
            "Subject": msg.subject,
            "TextPart": msg.body,
        }
        if msg.cc:
            payload["Cc"] = [{"Email": a} for a in msg.cc]
        if msg.bcc:
            payload["Bcc"] = [{"Email": a} for a in msg.bcc]
        # Attach an HTML alternative if one is present.
        for content, mimetype in getattr(msg, "alternatives", []) or []:
            if mimetype == "text/html":
                payload["HTMLPart"] = content
                break
        return payload

    def send_messages(self, email_messages) -> int:
        if not email_messages:
            return 0
        if not (self.api_key and self.api_secret):
            if not self.fail_silently:
                raise RuntimeError("Mailjet credentials are not configured (MAILJET_API_KEY / MAILJET_API_SECRET).")
            return 0

        messages = [p for m in email_messages if (p := self._message_to_payload(m))]
        if not messages:
            return 0

        body = json.dumps({"Messages": messages}).encode()
        req = urllib.request.Request(MAILJET_URL, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", self._auth_header())
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            log.warning("Mailjet send failed: %s %s", e.code, detail)
            if not self.fail_silently:
                raise
            return 0
        except Exception as e:  # network / timeout
            log.warning("Mailjet send error: %s", e)
            if not self.fail_silently:
                raise
            return 0

        sent = sum(1 for r in data.get("Messages", []) if r.get("Status") == "success")
        return sent
