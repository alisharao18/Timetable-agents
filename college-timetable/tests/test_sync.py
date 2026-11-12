from datetime import date
import pytest
from sqlalchemy import insert
import db
import finder
import importer
import sync
from conftest import DATA


@pytest.fixture
def e(engine):
    with engine.begin() as c:
        c.execute(insert(db.rooms), [
            dict(id=1, name="E.CR.501", kind="classroom", capacity=65, floor=5, lab_type=None),
            dict(id=2, name="E.CL.401", kind="lab", capacity=29, floor=4, lab_type="COMPUTER")])
    return engine


def status(e):
    return [(r["status"], r["note"]) for r in db.q(e, "select status, note from sheet_edits order by id")]


def test_edit_on_free_cell_is_applied(e):
    sync.add_edit(e, "MONDAY", "E.CR.501", "Mon", 10, None, "3B IT", "p@x")
    assert sync.process_inbox(e) == {"applied": 1, "rejected": 0}
    assert db.q(e, "select label, source from placements")[0] == {"label": "3B IT", "source": "sheet"}
    assert finder.find_rooms(e, date(2026, 11, 16), 10, seats=10) == []


def test_dated_booking_from_sheet_and_clash_is_rejected(e):
    assert finder.book(e, "E.CR.501", date(2026, 10, 26), 16, 1, "IT", "Dept meeting", "first@x")[0]
    sync.add_edit(e, "MONDAY", "E.CR.501", "Mon", 16, None, "Booked on 26.10.2026 for Prof Z", "second@x")
    sync.add_edit(e, "MONDAY", "E.CR.501", "Mon", 17, None, "Booked on 26.10.2026 for Prof Z", "second@x")
    assert sync.process_inbox(e) == {"applied": 1, "rejected": 1}
    st = status(e)
    assert st[0][0] == "rejected" and "already booked on 26 Oct 2026 at 16:00 by first@x" in st[0][1]
    who = {r["hour"]: r["booked_by"] for r in db.q(e, "select hour, booked_by from bookings")}
    assert who == {16: "first@x", 17: "second@x"}        # the original booking is untouched


def test_published_class_cannot_be_overwritten_from_the_sheet(e):
    with e.begin() as c:
        c.execute(insert(db.placements).values(room_id=1, day="Tue", hour=9, label="5B IT · DBMS", source="generated"))
    sync.add_edit(e, "TUESDAY", "E.CR.501", "Tue", 9, None, "7B IT", "p@x")
    sync.add_edit(e, "TUESDAY", "E.CR.501", "Tue", 9, None, "Booked on 27.10.2026 for X", "p@x")
    sync.add_edit(e, "TUESDAY", "E.CR.501", "Tue", 9, "5B IT", None, "p@x")
    assert sync.process_inbox(e) == {"applied": 0, "rejected": 3}
    assert db.q(e, "select label from placements")[0]["label"] == "5B IT · DBMS"


def test_lab_title_resolves_unknown_room_and_unreadable_text(e):
    sync.add_edit(e, "4th floor", "Comp Lab E. CL 401  (capacity 29) - COMPUTER DEPT", "Wed", 14, None, "7 BTI B", "p@x")
    sync.add_edit(e, "4th floor", "Comp Lab E. CL 999 (capacity 30)", "Wed", 14, None, "7 BTI B", "p@x")
    sync.add_edit(e, "MONDAY", "E.CR.501", "Mon", 12, None, "Booked for dept meeting", "p@x")
    sync.add_edit(e, "MONDAY", "E.CR.501", "Sun", 12, None, "x", "p@x")   # Sunday is a college day now
    assert sync.process_inbox(e) == {"applied": 3, "rejected": 1}
    assert db.q(e, "select room_id from placements where label='7 BTI B'")[0]["room_id"] == 2
    assert db.q(e, "select count(*) n from unresolved")[0]["n"] == 1
    assert "blocked until someone checks" in status(e)[2][1]


def test_clearing_a_cell_frees_it(e):
    sync.add_edit(e, "MONDAY", "E.CR.501", "Mon", 10, None, "3B IT", "p@x")
    sync.add_edit(e, "MONDAY", "E.CR.501", "Mon", 10, "3B IT", None, "p@x")
    sync.add_edit(e, "MONDAY", "E.CR.501", "Mon", 15, None, "Booked on 26.10.2026 for A", "p@x")
    sync.add_edit(e, "MONDAY", "E.CR.501", "Mon", 15, "Booked on 26.10.2026 for A", None, "p@x")
    assert sync.process_inbox(e) == {"applied": 4, "rejected": 0}
    assert db.q(e, "select count(*) n from placements")[0]["n"] == 0
    assert db.q(e, "select count(*) n from bookings")[0]["n"] == 0
    assert sync.process_inbox(e) == {"applied": 0, "rejected": 0}     # nothing is processed twice


def test_reupload_diff(engine):
    res = importer.parse_classrooms(DATA / "classrooms.xlsx", 2026)
    importer.load_into_db(engine, res)
    assert len(sync.diff_upload(engine, res)) == 0
    res.weekly[0]["label"] = "CHANGED"
    gone = res.weekly.pop(5)
    d = sync.diff_upload(engine, res)
    assert sorted(d["change"]) == ["changed", "removed"]
    assert d[d["change"] == "removed"].iloc[0]["was"] == gone["label"]
