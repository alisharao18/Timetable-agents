import threading
from datetime import date
import pytest
from sqlalchemy import insert
import db
import finder

MON = date(2026, 11, 16)          # a Monday
NEXT_MON = date(2026, 11, 23)


@pytest.fixture
def e(engine):
    with engine.begin() as c:
        c.execute(insert(db.departments), [{"id": 1, "name": "IT"}, {"id": 2, "name": "Civil"}])
        c.execute(insert(db.rooms), [
            dict(id=1, name="A", lab_type=None, kind="classroom", room_type="STEP", capacity=60, floor=5),
            dict(id=2, name="B", lab_type=None, kind="classroom", room_type="FLAT", capacity=80, floor=4),
            dict(id=3, name="C", lab_type=None, kind="classroom", room_type="STEP", capacity=70, floor=6),
            dict(id=4, name="L", kind="lab", room_type="LAB", capacity=30, floor=5, lab_type="COMPUTER")])
        c.execute(insert(db.department_rooms), [{"department_id": 1, "room_id": 3}])
        c.execute(insert(db.placements).values(room_id=1, day="Mon", hour=10, label="3B IT", source="sheet"))
    return engine


def names(rows):
    return [r["room"] for r in rows]


def test_weekly_class_blocks_every_week(e):
    assert "A" not in names(finder.find_rooms(e, MON, 10, seats=50))
    assert "A" not in names(finder.find_rooms(e, NEXT_MON, 10, seats=50))
    assert "A" in names(finder.find_rooms(e, MON, 11, seats=50))
    assert "A" not in names(finder.find_rooms(e, MON, 9, duration=2, seats=50))


def test_dated_booking_blocks_only_that_date(e):
    ok, _ = finder.book(e, "B", MON, 14, 2, "IT", "Guest talk", "a@x")
    assert ok
    assert "B" not in names(finder.find_rooms(e, MON, 15, seats=50))
    assert "B" in names(finder.find_rooms(e, NEXT_MON, 15, seats=50))


def test_ranking_own_department_then_snug_fit(e):
    assert names(finder.find_rooms(e, MON, 12, seats=55)) == ["A", "C", "B"]
    assert names(finder.find_rooms(e, MON, 12, seats=55, department="IT")) == ["C", "A", "B"]
    assert names(finder.find_rooms(e, MON, 12, seats=65)) == ["C", "B"]
    assert names(finder.find_rooms(e, MON, 12, seats=10, room_type="flat")) == ["B"]
    assert names(finder.find_rooms(e, MON, 12, kind="lab", seats=25, lab_type="computer")) == ["L"]


def test_two_people_at_once_exactly_one_wins(e):
    results = []
    gate = threading.Barrier(8)

    def go(i):
        gate.wait()
        results.append(finder.book(e, "A", MON, 16, 2, "IT", "Meeting", f"u{i}@x")[0])

    ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert results.count(True) == 1
    assert db.q(e, "select count(*) n from bookings where room_id=1")[0]["n"] == 2


def test_overlapping_booking_is_refused_whole(e):
    assert finder.book(e, "C", MON, 9, 1, "IT", "x", "a@x")[0]
    ok, msg = finder.book(e, "C", MON, 8, 3, "IT", "y", "b@x")
    assert not ok and "no longer free" in msg
    assert db.q(e, "select count(*) n from bookings where room_id=3")[0]["n"] == 1


def test_bad_requests_get_plain_messages(e):
    assert "Sunday" in finder.book(e, "A", date(2026, 11, 15), 9, 1, "IT", "x", "a@x")[1]
    assert "7 AM" in finder.book(e, "A", MON, 18, 2, "IT", "x", "a@x")[1]
    assert "no room" in finder.book(e, "Z", MON, 9, 1, "IT", "x", "a@x")[1]
    assert finder.find_rooms(e, date(2026, 11, 15), 9) == []


def test_cancel_rules(e):
    finder.book(e, "B", MON, 9, 1, "IT", "x", "a@x")
    key = finder.list_bookings(e, user="a@x")[0]["booking_key"]
    assert not finder.cancel(e, key, "other@x")[0]
    assert finder.cancel(e, key, "admin@x", is_admin=True)[0]
    assert "B" in names(finder.find_rooms(e, MON, 9, seats=1))


def test_nearest_free_suggests_other_times(e):
    for r in "ABC":
        finder.book(e, r, MON, 13, 1, "IT", "x", "a@x")
    assert finder.find_rooms(e, MON, 13, seats=50) == []
    alt = finder.nearest_free(e, MON, 13, seats=50, limit=3)
    assert len(alt) == 3 and alt[0]["date"] == MON and alt[0]["start_hour"] in (12, 14)
