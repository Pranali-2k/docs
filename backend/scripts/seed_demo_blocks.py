"""Keep some of Dr. Marcus Patel's time busy so the availability flow can be shown without setting it up first.

Safe to run again: a block that is already there is left alone. Run from the backend folder:

    .venv\Scripts\python.exe -m scripts.seed_demo_blocks
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Provider, ProviderBlock
from app.services import blocks
from app.services.scheduling import clinic_now

#: (days from the next Monday, start, end, reason)
DEMO = [(0, time(10), time(12), "Surgery"), (2, time(14), time(16), "Team meeting"), (4, time(9), time(11), "Clinic outreach")]


def main() -> None:
    today = clinic_now().date()
    monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    with SessionLocal() as db:
        doctor = db.scalar(select(Provider).where(Provider.full_name.like("%Marcus Patel%")))
        if doctor is None:
            print("Dr. Marcus Patel is not in this database.")
            return
        for offset, start, end, reason in DEMO:
            day = monday + timedelta(days=offset)
            s, e = datetime.combine(day, start), datetime.combine(day, end)
            exists = db.scalar(select(ProviderBlock).where(
                ProviderBlock.provider_id == doctor.id, ProviderBlock.start_at <= s, ProviderBlock.end_at >= e))
            if exists:
                print(f"already busy: {day} {start:%H:%M}-{end:%H:%M}")
                continue
            blocks.add_block(db, doctor, s, e, reason=reason, actor="demo seed")
            print(f"busy: {doctor.full_name} {day:%a %b %d} {start:%H:%M}-{end:%H:%M} ({reason})")
        db.commit()


if __name__ == "__main__":
    main()
