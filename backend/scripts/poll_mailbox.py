"""Poll the scheduling mailbox once (Outlook via Graph, or IMAP) and process new emails.

Schedule it with Windows Task Scheduler / cron, or set MAIL_POLL_INTERVAL_SECONDS
to let the API poll in the background.
"""
from app.db import SessionLocal
from app.services import email_gateway, pipeline


def main() -> None:
    with SessionLocal() as db:
        created = email_gateway.poll_mailbox(db)
        for req in created:
            pipeline.process_request(db, req, actor="ai-assistant")
            db.commit()
            print(f"{req.reference}: {req.status} {[r['code'] for r in req.review_reasons or []]}")
        print(f"Processed {len(created)} new email(s)")


if __name__ == "__main__":
    main()
