"""Finding mail by day, on the clinic's (Los Angeles) calendar."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.models import EmailMessage, PortalRequest
from app.api.requests import _day_bounds
from datetime import date

LA = ZoneInfo("America/Los_Angeles")


def inject(client, key):
    r = client.post(f"/api/demo/samples/{key}")
    assert r.status_code == 201, r.text
    return r.json()["request"]


def test_the_clinic_is_in_los_angeles() -> None:
    assert get_settings().clinic_timezone == "America/Los_Angeles"


def test_a_day_runs_from_midnight_to_midnight_in_los_angeles() -> None:
    start, end = _day_bounds(date(2026, 10, 16))  # PDT: UTC-7
    assert (start, end) == (datetime(2026, 10, 16, 7, 0), datetime(2026, 10, 17, 7, 0))  # naive UTC on SQLite
    winter_start, winter_end = _day_bounds(date(2026, 12, 1))  # PST: UTC-8
    assert winter_start.hour == 8 and (winter_end - winter_start) == timedelta(hours=24)
    start, end = _day_bounds(date(2026, 11, 1))  # the day the clocks go back is 25 hours long
    assert (end - start) == timedelta(hours=25)


def test_requests_can_be_listed_for_one_day_and_the_days_are_offered(client, db) -> None:
    a = inject(client, "auto_book")
    b = inject(client, "new_patient")
    # Put the two emails on different Los Angeles days: 11:30 pm on the 15th (06:30 UTC on the 16th) and 8 am on the 16th.
    late = db.get(PortalRequest, a["id"])
    late.source_email.received_at = datetime(2026, 10, 16, 6, 30, tzinfo=timezone.utc)
    morning = db.get(PortalRequest, b["id"])
    morning.source_email.received_at = datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)
    db.commit()

    on_15 = client.get("/api/requests", params={"day": "2026-10-15"}).json()
    on_16 = client.get("/api/requests", params={"day": "2026-10-16"}).json()
    assert [r["id"] for r in on_15] == [a["id"]] and [r["id"] for r in on_16] == [b["id"]]  # not split by UTC midnight
    assert client.get("/api/requests", params={"day": "2026-10-17"}).json() == []
    assert len(client.get("/api/requests").json()) >= 2  # no day: everything, as before

    days = {d["day"]: d for d in client.get("/api/requests/days", params={"days": 366}).json()}
    assert days["2026-10-15"]["count"] == 1 and days["2026-10-16"]["count"] == 1
    assert list(days) == sorted(days, reverse=True)
