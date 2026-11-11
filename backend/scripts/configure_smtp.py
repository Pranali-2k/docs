"""Set up SMTP (e.g. Microsoft 365) and prove it with a real test email.

    python -m scripts.configure_smtp                  # prompts; saves settings only if the test mail is accepted
    python -m scripts.configure_smtp --test-only      # re-send a test mail with the settings already in .env
    python -m scripts.configure_smtp --enable         # also switch EMAIL_OUTBOUND_MODE to smtp

The password is typed hidden, never printed, and only written to backend/.env after the
server accepted the test message. A backup of .env is kept as .env.bak.
"""
from __future__ import annotations

import argparse
import getpass
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from app.services.mailer import send_mail

ENV = Path(__file__).resolve().parents[1] / ".env"
RUN_LOG = Path(__file__).resolve().parents[2] / "logs" / "smtp_setup.log"


def run_log(line: str) -> None:
    """Append one line per run (never the password) so failures can be diagnosed later."""
    try:
        RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
        with RUN_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {line}\n")
    except OSError:
        pass

PRESETS = {  # domain hints -> (host, port, security)
    "office365": ("smtp.office365.com", 587, "starttls"),
    "gmail": ("smtp.gmail.com", 587, "starttls"),
}


def write_env(values: dict[str, str]) -> None:
    content = ENV.read_text(encoding="utf-8-sig")
    shutil.copyfile(ENV, ENV.with_suffix(".bak"))
    for key, value in values.items():
        line = f"{key}={value}"
        if re.search(rf"(?m)^\s*{key}\s*=.*$", content):
            content = re.sub(rf"(?m)^\s*{key}\s*=.*$", lambda _: line, content, count=1)
        else:
            content = content.rstrip("\n") + "\n" + line + "\n"
    ENV.write_text(content, encoding="utf-8")


def test_mail(settings, to: str) -> bool:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result = send_mail(
        to,
        f"Docs Health SMTP test ({stamp})",
        f"This is a test email from the Docs Health scheduling portal.\n\n"
        f"Server: {settings.smtp_host}:{settings.smtp_port} ({settings.smtp_security})\n"
        f"Sent as: {settings.smtp_from}\nTime: {stamp}\n\nIf you received this, SMTP delivery works.",
        body_html=(f"<p>This is a test email from the <b>Docs Health</b> scheduling portal.</p>"
                   f"<p>Server: {settings.smtp_host}:{settings.smtp_port} ({settings.smtp_security})<br>"
                   f"Sent as: {settings.smtp_from}<br>Time: {stamp}</p><p>If you received this, SMTP delivery works.</p>"),
        settings=settings,
    )
    run_log(f"test to={to} host={settings.smtp_host}:{settings.smtp_port} user={settings.smtp_username or '-'} "
            f"from={settings.smtp_from} -> " + ("ACCEPTED " + str(result.message_id) if result.ok else
            f"FAILED [{result.error_kind}] {result.error} | server: {result.server_reply or '-'}"))
    if result.ok:
        print(f"  ACCEPTED by {settings.smtp_host} for {', '.join(result.accepted)} "
              f"(attempts: {result.attempts}, {result.elapsed_ms} ms, Message-ID {result.message_id})")
        if result.refused:
            print(f"  Refused recipients: {result.refused}")
        return True
    print(f"  FAILED [{result.error_kind}] {result.error}")
    if result.server_reply:
        print(f"  Server said: {result.server_reply}")
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", default="pranali@ashunya.com", help="test recipient")
    ap.add_argument("--test-only", action="store_true")
    ap.add_argument("--enable", action="store_true", help="set EMAIL_OUTBOUND_MODE=smtp after a successful test")
    args = ap.parse_args()
    current = get_settings()

    if args.test_only:
        print(f"Sending a test email to {args.to} with the settings in .env ...")
        return 0 if test_mail(current, args.to) else 1

    print("Docs Health - SMTP setup (Ctrl+C to cancel)\n")
    run_log(f"setup started (project {ENV.parent})")
    user = input("Mailbox / SMTP username [pranali@ashunya.com]: ").strip() or "pranali@ashunya.com"
    host, port, security = PRESETS["office365"]
    host = input(f"SMTP host [{host}]: ").strip() or host
    port = int(input(f"SMTP port [{port}]: ").strip() or port)
    security = input(f"Security starttls/ssl [{security}]: ").strip() or security
    sender = input(f"Send as (From address) [{user}]: ").strip() or user
    password = getpass.getpass("Mailbox password or app password (hidden): ")
    candidate = current.model_copy(update=dict(
        smtp_host=host, smtp_port=port, smtp_security=security, smtp_username=user, smtp_password=password,
        smtp_from=sender, smtp_max_attempts=2))

    print(f"\nSending a test email to {args.to} ...")
    if not test_mail(candidate, args.to):
        print("\nNothing was saved. Fix the problem above and run this again.")
        return 1
    values = {"SMTP_HOST": host, "SMTP_PORT": str(port), "SMTP_SECURITY": security, "SMTP_USERNAME": user,
              "SMTP_PASSWORD": password, "SMTP_FROM": sender, "SCHEDULING_MAILBOX": sender}
    if args.enable:
        values["EMAIL_OUTBOUND_MODE"] = "smtp"
    write_env(values)
    run_log(f"saved SMTP settings for {user} (outbound={'smtp' if args.enable else 'unchanged'})")
    print(f"\nSaved SMTP settings to {ENV} (backup: .env.bak)."
          + (" EMAIL_OUTBOUND_MODE=smtp: replies and password-reset links are now really sent."
             if args.enable else " Outbound mode unchanged; add --enable to send app emails via SMTP.")
          + " Restart the API.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelled. Nothing was changed.")
        sys.exit(130)
