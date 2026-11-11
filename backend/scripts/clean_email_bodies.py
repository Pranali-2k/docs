"""Convert already-stored HTML email bodies to readable text (new mail is cleaned at intake).

    python -m scripts.clean_email_bodies --dry-run   # report only
    python -m scripts.clean_email_bodies             # update + audit entry per message (original kept in body_raw)

Only bodies that look like HTML are touched; plain-text bodies are left as they are.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models import EmailMessage
from app.services import audit
from app.services.text_clean import clean_body, looks_like_html


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    changed = failed = 0
    with SessionLocal() as db:
        ids = db.scalars(select(EmailMessage.id).where(EmailMessage.direction == "inbound").order_by(EmailMessage.id)).all()
        for mid in ids:
            try:
                m = db.get(EmailMessage, mid)
                if not looks_like_html(m.body_text):
                    continue
                before = len(m.body_text)
                text = clean_body(m.body_text)
                changed += 1
                print(f"email {m.id}: {before} -> {len(text)} chars")
                if not args.dry_run:
                    m.body_raw = m.body_raw or m.body_text  # keep the original
                    m.body_text = text
                    audit.record(db, "email", m.id, "email_body_cleaned", "maintenance", chars_before=before,
                                 chars_after=len(text))
                    db.commit()
            except Exception as exc:  # keep going; report at the end
                db.rollback()
                failed += 1
                print(f"email {mid}: FAILED ({type(exc).__name__}: {exc})")
    print(f"\n{'would clean' if args.dry_run else 'cleaned'} {changed} message(s); {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
