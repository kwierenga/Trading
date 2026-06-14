"""
Email Notifier
Sends short plain-text emails via SMTP. Used by morning_routine.py and
eod_routine.py to deliver the daily plan + EOD review to Klaas's inbox.

Defaults to Gmail SMTP. Override via EMAIL_SMTP_HOST / EMAIL_SMTP_PORT in .env
to use a different provider (Outlook, etc.).

Note: Yahoo no longer reliably supports SMTP app passwords on consumer accounts.
Use Gmail or Outlook.com.

Setup (one-time, Gmail):
  1. Go to https://myaccount.google.com/security
  2. Turn on 2-Step Verification (required to generate app passwords).
  3. Visit https://myaccount.google.com/apppasswords → name it "Trading System"
     → copy the 16-char password.
  4. Add to .env:

       EMAIL_USER=klaas.trading@gmail.com
       EMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx
       EMAIL_TO=klaas.trading@gmail.com

If env vars aren't set, send_email() returns False quietly — routines still run
and write to JOURNAL.md, just no email gets sent.
"""

import mimetypes
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import List, Optional

# Load .env so env vars are available whether this module is run standalone
# (e.g., `python email_notifier.py "test" "hello"`) or imported by routines that
# already load it via config.py. load_dotenv() is idempotent.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# Defaults are Gmail SMTP. Override per-environment via EMAIL_SMTP_HOST / EMAIL_SMTP_PORT.
DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 587
SMTP_TIMEOUT_SECONDS = 20


def send_email(
    subject: str,
    body: str,
    to_addr: Optional[str] = None,
    attachments: Optional[List[str]] = None,
) -> bool:
    """
    Send a plain-text email, optionally with file attachments. Returns True on
    success, False on failure (including missing config). Failures are logged to
    stdout but don't raise.

    attachments: list of file paths. Missing/unreadable files are skipped with a
    warning rather than failing the send (the email body still goes out).
    """
    user = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_APP_PASSWORD")
    recipient = to_addr or os.getenv("EMAIL_TO") or user

    if not user or not password:
        print("  Email skipped: EMAIL_USER or EMAIL_APP_PASSWORD missing from .env")
        return False

    if not recipient:
        print("  Email skipped: no recipient (set EMAIL_TO or EMAIL_USER in .env)")
        return False

    host = os.getenv("EMAIL_SMTP_HOST", DEFAULT_SMTP_HOST)
    try:
        port = int(os.getenv("EMAIL_SMTP_PORT", str(DEFAULT_SMTP_PORT)))
    except ValueError:
        port = DEFAULT_SMTP_PORT

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = recipient
    msg.set_content(body)

    for path_str in attachments or []:
        path = Path(path_str)
        if not path.is_file():
            print(f"  Attachment skipped (not found): {path_str}")
            continue
        try:
            data = path.read_bytes()
        except OSError as e:
            print(f"  Attachment skipped ({type(e).__name__}): {path_str}")
            continue
        ctype, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (ctype.split("/", 1) if ctype else ("application", "octet-stream"))
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            smtp.starttls(context=ctx)
            smtp.login(user, password)
            smtp.send_message(msg)
        print(f"  Email sent: {subject!r} -> {recipient}")
        return True
    except (smtplib.SMTPException, OSError) as e:
        print(f"  Email failed ({type(e).__name__}): {e}  [host={host}:{port}]")
        return False


if __name__ == "__main__":
    # Test: send a quick smoke email to verify .env config.
    import sys
    subject = sys.argv[1] if len(sys.argv) > 1 else "Trading: email test"
    body = sys.argv[2] if len(sys.argv) > 2 else "If you see this, Gmail SMTP is wired correctly."
    ok = send_email(subject, body)
    sys.exit(0 if ok else 1)
