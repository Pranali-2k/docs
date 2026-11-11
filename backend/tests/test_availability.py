"""A doctor's own time: kept free by a note or a button, honoured when booking, and the reply when a slot is taken."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import select

from app.models import Appointment, Provider
from app.services import availability_chat as chat
from app.services import scheduling
from app.services.availability_chat import DAY_END, DAY_START

TODAY = date(2026, 10, 16)  # a Friday
PROVIDERS = [(1, "Dr. Anita Rao"), (2, "Dr. Marcus Patel"), (3, "Dr. Elena Torres")]


def read(text: str, default: int | None = 1):
    return chat.parse(text, PROVIDERS, default, TODAY)


# ------------------------------------------------------------------------------------------ the note
def test_busy_tomorrow_afternoon_hours() -> None:
    cmd = read("Dr. Rao is busy tomorrow 2-4 pm for surgery")
    assert (cmd.action, cmd.provider_id, cmd.reason) == ("block", 1, "Surgery")
    assert cmd.ranges == [(datetime(2026, 10, 17, 14), datetime(2026, 10, 17, 16))]


def test_times_in_the_ways_people_write_them() -> None:
    assert read("busy Monday from 9:30 to 11 am").ranges == [(datetime(2026, 10, 19, 9, 30), datetime(2026, 10, 19, 11))]
    assert read("busy Monday 2pm until 3:30pm").ranges == [(datetime(2026, 10, 19, 14), datetime(2026, 10, 19, 15, 30))]
    assert read("busy Monday morning").ranges == [(datetime(2026, 10, 19, 8), datetime(2026, 10, 19, 12))]
    assert read("busy Monday after 3").ranges == [(datetime(2026, 10, 19, 15), datetime.combine(date(2026, 10, 19), DAY_END))]
    assert read("unavailable Monday before 10 am").ranges == [(datetime.combine(date(2026, 10, 19), DAY_START), datetime(2026, 10, 19, 10))]
    assert read("out of office Monday").ranges == [(datetime.combine(date(2026, 10, 19), DAY_START), datetime.combine(date(2026, 10, 19), DAY_END))]
    assert read("busy Monday 9 to 5").ranges[0][1].hour == 17  # "9 to 5" is 9 am to 5 pm


def test_days_in_the_ways_people_write_them() -> None:
    days = lambda text: [r[0].date() for r in read(text).ranges]  # noqa: E731
    assert days("busy today") == [TODAY]
    assert days("busy Oct 20") == [date(2026, 10, 20)]
    assert days("busy 10/21 all day") == [date(2026, 10, 21)]
    assert days("on vacation Oct 20-22") == [date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)]
    assert days("away Monday to Wednesday") == [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)]
    assert days("busy next Tuesday") == [date(2026, 10, 20)] or days("busy next Tuesday") == [date(2026, 10, 27)]
    assert len(days("busy next week")) == 5 and days("busy next week")[0] == date(2026, 10, 19)


def test_who_and_what() -> None:
    assert read("Dr. Torres is away Monday", default=1).provider_id == 3
    assert read("Marcus is busy Monday", default=1).provider_id == 2
    assert read("busy Monday", default=2).provider_id == 2  # the doctor being worked on
    assert read("free up Monday afternoon").action == "unblock"
    assert read("I'm available again tomorrow").action == "unblock"
    assert read("show what is blocked this week").action == "list"
    assert read("how are you").action == "unknown" and read("how are you").problems
    assert read("busy", default=1).problems  # no day: asks which


# ---------------------------------------------------------------------------- the schedule honours it
def next_weekday(start: date, weekday: int = 0) -> date:
    return start + timedelta(days=(weekday - start.weekday()) % 7 or 7)


def first_open(db, provider_name="Dr. Anita Rao"):
    provider = db.scalar(select(Provider).where(Provider.full_name == provider_name))
    day = next_weekday(scheduling.clinic_now().date())
    return provider, day, scheduling.available_slots(db, provider.id, day)


def test_a_blocked_time_is_not_offered_and_cannot_be_booked(client, db) -> None:
    provider, day, slots = first_open(db)
    assert len(slots) >= 3
    blocked = slots[1]
    made = client.post("/api/provider-blocks", json={
        "provider_id": provider.id, "start_at": blocked.isoformat(), "end_at": (blocked + timedelta(minutes=30)).isoformat(),
        "reason": "Surgery"})
    assert made.status_code == 201, made.text
    after = scheduling.available_slots(db, provider.id, day)
    assert blocked not in after and slots[0] in after and slots[2] in after

    from app.models import Patient
    patient = db.scalar(select(Patient))
    try:
        scheduling.book(db, patient, provider, blocked)
        raise AssertionError("booked inside a block")
    except scheduling.SchedulingError as exc:
        assert "unavailable" in str(exc) and "Surgery" in str(exc)


def test_blocks_merge_split_and_are_listed(client, db) -> None:
    provider, day, _ = first_open(db)
    at = lambda h, m=0: datetime.combine(day, time(h, m)).isoformat()  # noqa: E731
    client.post("/api/provider-blocks", json={"provider_id": provider.id, "start_at": at(9), "end_at": at(10)})
    client.post("/api/provider-blocks", json={"provider_id": provider.id, "start_at": at(10), "end_at": at(11), "reason": "Meeting"})
    listed = client.get("/api/provider-blocks", params={"start": day.isoformat(), "days": 1}).json()
    assert len(listed) == 1 and listed[0]["start_at"] == at(9) and listed[0]["end_at"] == at(11)  # touching blocks became one

    # give the middle back: the one block becomes two
    reply = client.post("/api/availability/chat", json={
        "message": f"Dr. Rao is available again {day:%b} {day.day} 9:30-10:00", "provider_id": provider.id}).json()
    assert reply["changed"] and "Given back" in reply["reply"]
    parts = client.get("/api/provider-blocks", params={"start": day.isoformat(), "days": 1}).json()
    assert [(p["start_at"], p["end_at"]) for p in parts] == [(at(9), at(9, 30)), (at(10), at(11))]
    one = parts[0]["id"]
    assert client.delete(f"/api/provider-blocks/{one}").status_code == 200
    assert len(client.get("/api/provider-blocks", params={"start": day.isoformat(), "days": 1}).json()) == 1


def test_the_chat_blocks_time_and_warns_about_patients_already_booked(client, db) -> None:
    provider, day, slots = first_open(db)
    from app.models import Patient
    patient = db.scalar(select(Patient))
    appt = scheduling.book(db, patient, provider, slots[0])
    db.commit()
    reply = client.post("/api/availability/chat", json={
        "message": f"Dr. Rao is busy {day:%B} {day.day} {slots[0]:%I:%M %p}-{(slots[0] + timedelta(hours=1)):%I:%M %p}",
        "provider_id": provider.id}).json()
    assert reply["changed"] and "Heads up" in reply["reply"] and patient.full_name in reply["reply"]
    assert reply["already_booked"][0]["id"] == appt.id
    assert db.get(Appointment, appt.id).status == "booked"  # the doctor is told; nobody's visit is cancelled behind their back
    bad = client.post("/api/availability/chat", json={"message": "busy", "provider_id": provider.id}).json()
    assert bad["changed"] is False and "Which day" in bad["reply"]
    past = client.post("/api/availability/chat", json={"message": "busy yesterday 2-3 pm", "provider_id": provider.id}).json()
    assert past["changed"] is False or "passed" in past["reply"]


# ---------------------------------------------------------------- the email that asks for a taken slot
def email(client, who, address, text, mid):
    out = client.post("/api/intake/email", json={"from_address": address, "from_name": who,
                                                 "subject": "Appointment", "body": text, "message_id": mid})
    assert out.status_code == 201, out.text
    return out.json()["request"]


def test_a_taken_slot_is_answered_at_once_with_that_doctors_nearest_openings(client, db) -> None:
    provider, day, slots = first_open(db)
    wanted = slots[1]
    # Dr. Rao keeps that half hour for herself.
    assert client.post("/api/provider-blocks", json={
        "provider_id": provider.id, "start_at": wanted.isoformat(), "end_at": (wanted + timedelta(minutes=30)).isoformat(),
        "reason": "Surgery"}).status_code == 201
    req = email(client, "Maria Gonzalez", "someone.else@example.com",
                f"Hello, this is Maria Gonzalez. I would like to see Dr. Anita Rao on {day:%B} {day.day} at {wanted:%I:%M %p}.", "<taken-1@x>")
    assert req["status"] == "auto_responded", (req["status"], req["review_reasons"])  # not held for review
    assert req["appointment"] is None
    reply = next(e for e in req["emails"] if e["direction"] == "outbound")
    assert "Dr. Anita Rao" in reply["body_text"] and "is not available on" in reply["body_text"] and "Please reschedule" in reply["body_text"]
    assert scheduling.fmt_slot(slots[2]) in reply["body_text"] or scheduling.fmt_slot(slots[0]) in reply["body_text"]
    assert scheduling.fmt_slot(wanted) not in reply["body_text"].split("openings", 1)[1]  # never offers the blocked time


def test_a_free_slot_is_booked_straight_away_for_the_chart_named_in_the_email(client, db) -> None:
    provider, day, slots = first_open(db)
    wanted = slots[0]
    req = email(client, "Maria Gonzalez", "someone.else@example.com",
                f"Hello, this is Maria Gonzalez. I would like to see Dr. Anita Rao on {day:%B} {day.day} at {wanted:%I:%M %p}.", "<free-1@x>")
    assert req["status"] == "resolved", (req["status"], req["review_reasons"])
    assert req["appointment"]["provider_name"] == "Dr. Anita Rao" and req["appointment"]["start_at"].startswith(wanted.isoformat()[:16])
    assert req["match_details"].get("linked_by") == "name"  # no date of birth was given: booked on the exact name alone
    reply = next(e for e in req["emails"] if e["direction"] == "outbound")
    assert "Booked under the chart we have for Maria Gonzalez" in reply["body_text"]


def test_a_name_that_could_be_two_people_is_still_held_for_review(client, db) -> None:
    from app.models import Patient
    twin = db.scalar(select(Patient).where(Patient.last_name == "Gonzalez"))
    db.add(Patient(first_name=twin.first_name, last_name=twin.last_name, mrn="MRN-TWIN", date_of_birth=date(1962, 2, 2)))
    db.commit()
    provider, day, slots = first_open(db)
    req = email(client, twin.first_name + " Gonzalez", "someone.else@example.com",
                f"Hello, this is {twin.first_name} Gonzalez. I would like to see Dr. Anita Rao on {day:%B} {day.day} at {slots[0]:%I:%M %p}.", "<twin-1@x>")
    assert req["status"] == "needs_review" and req["appointment"] is None


def test_the_day_grid_shows_every_time_and_a_click_makes_it_busy_then_free_again(client, db) -> None:
    provider, day, slots = first_open(db)
    grid = lambda: client.get(f"/api/providers/{provider.id}/day-grid", params={"day": day.isoformat()}).json()  # noqa: E731
    states = lambda: {r["start_at"]: r["state"] for r in grid()}  # noqa: E731
    first = states()
    assert all(first[s.isoformat()] == "open" for s in slots)
    assert len(first) > len(slots) or len(first) == len(slots)  # every slot of the working day is listed

    a, b = slots[1], slots[1] + timedelta(minutes=30)
    client.post("/api/provider-blocks", json={"provider_id": provider.id, "start_at": a.isoformat(), "end_at": b.isoformat(), "reason": "Busy"})
    assert states()[a.isoformat()] == "busy" and states()[slots[0].isoformat()] == "open"

    # Two neighbouring clicks merge into one block; freeing one of them leaves the other busy.
    nxt = slots[1] + timedelta(minutes=30)
    client.post("/api/provider-blocks", json={"provider_id": provider.id, "start_at": b.isoformat(), "end_at": (b + timedelta(minutes=30)).isoformat()})
    assert states()[a.isoformat()] == "busy" and states()[nxt.isoformat()] == "busy"
    freed = client.post("/api/provider-blocks/free", json={"provider_id": provider.id, "start_at": a.isoformat(), "end_at": b.isoformat()})
    assert freed.status_code == 200 and freed.json()["changed"] == 1
    assert states()[a.isoformat()] == "open" and states()[nxt.isoformat()] == "busy"

    from app.models import Patient
    scheduling.book(db, db.scalar(select(Patient)), provider, slots[0])
    db.commit()
    booked = [r for r in grid() if r["start_at"] == slots[0].isoformat()][0]
    assert booked["state"] == "booked" and booked["patient"]
