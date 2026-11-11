"""Record already-skipped mailbox messages once each (fixes over-counted "Internal skipped" totals).

    python -m scripts.backfill_mailbox_skips            # all enabled Microsoft 365 mailboxes
    python -m scripts.backfill_mailbox_skips --dry-run

Lists every message in each mailbox folder, and for each one that was not imported and matches a skip rule
(skip-listed sender, the clinic's own mailbox, auto-reply/bulk), stores one row. Safe to run repeatedly.
"""
from __future__ import annotations

import argparse
import collections
import sys
from datetime import datetime

from sqlalchemy import select

from app.db import SessionLocal
from app.models import EmailMessage, IntegrationMailbox
from app.services import msgraph
from app.services.email_gateway import _skip_reason
from app.services.mailbox_sync import own_addresses, parse_skip_list, record_skip, sender_filtered, skip_counts


def all_messages(db, mb):
    path = msgraph.resolve_folder(db, mb.address, mb.folder)
    url, params = f"{path}/messages", {"$top": 200, "$select": "id,from,receivedDateTime,internetMessageHeaders"}
    while url:
        data = msgraph.graph(db, "GET", url, params=params)
        yield from data.get("value", [])
        nxt, params = data.get("@odata.nextLink"), None
        url = nxt.split("/v1.0", 1)[1] if nxt else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    with SessionLocal() as db:
        own = own_addresses(db)
        for mb in db.scalars(select(IntegrationMailbox).where(IntegrationMailbox.enabled.is_(True))):
            try:
                imported = set(db.scalars(select(EmailMessage.provider_message_id).where(EmailMessage.mailbox_id == mb.id)))
                skip = parse_skip_list(mb.skip_sender_domains)
                tally = collections.Counter()
                for m in all_messages(db, mb):
                    if m["id"] in imported:
                        tally["imported"] += 1
                        continue
                    addr = (((m.get("from") or {}).get("emailAddress") or {}).get("address") or "").lower()
                    headers = {h["name"]: h["value"] for h in (m.get("internetMessageHeaders") or [])}
                    internal = sender_filtered(addr, skip)
                    kind, reason = ("own_mailbox", "sent by one of the clinic's own mailboxes") if addr in own else \
                        ("internal", internal) if internal else ("automated", _skip_reason(addr, headers))
                    if not reason:
                        tally["not handled yet"] += 1
                        continue
                    tally[kind] += 1
                    if not args.dry_run:
                        received = datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00"))
                        record_skip(db, mb, m["id"], kind, reason, addr, received)
                if not args.dry_run:
                    mb.skipped_internal_total = skip_counts(db, mb.id).get("internal", 0)
                    db.commit()
                print(f"{mb.address}: {dict(tally)}" + (" (dry run)" if args.dry_run else ""))
            except Exception as exc:  # one mailbox failing must not stop the others
                db.rollback()
                print(f"{mb.address}: FAILED ({type(exc).__name__}: {exc})")
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
