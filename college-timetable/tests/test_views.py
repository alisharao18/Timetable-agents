import io
from datetime import date
import pandas as pd
import pytest
import db
import finder
import sample_data
import solver
import views
from constants import hour_label


@pytest.fixture(scope="module")
def e(tmp_path_factory):
    eng = db.get_engine(f"sqlite:///{tmp_path_factory.mktemp('v')/'v.db'}")
    db.init_db(eng)
    assert sample_data.make_college(eng, seed=2, scale=0.12) == []
    p, rows, unplaced, _ = solver.generate(eng)
    assert not unplaced
    db.replace_placements(eng, rows, "generated")
    return eng


def filled(table):
    return int((table != "").sum().sum())


def test_teacher_view_matches_load(e):
    for t in db.q(e, "select t.name, sum(l.hours) h from teachers t join teaching_load l on l.teacher_id=t.id group by t.name"):
        assert filled(views.grid(e, "teacher", t["name"])) == t["h"]


def test_student_view_division_shows_its_batches_and_batch_shows_division(e):
    div = db.q(e, "select g.id, g.code from class_groups g where g.parent_id is null limit 1")[0]
    batch = db.q(e, "select id, code from class_groups where parent_id=:p limit 1", p=div["id"])[0]
    own = db.q(e, "select coalesce(sum(hours),0) h from teaching_load where group_id=:g", g=div["id"])[0]["h"]
    b_h = db.q(e, "select coalesce(sum(hours),0) h from teaching_load where group_id=:g", g=batch["id"])[0]["h"]
    t = views.grid(e, "student", batch["code"])
    assert filled(t) == own + b_h                      # a student's real week
    d = views.grid(e, "student", div["code"])
    assert f"[{batch['code']}]" in "\n".join(d.values.ravel())


def test_room_and_department_views_cover_every_placement(e):
    total = db.q(e, "select count(*) n from placements")[0]["n"]
    assert sum(filled(views.grid(e, "room", r)) for r in views.keys(e, "room")) == total
    lines = sum(len([x for x in cell.split("\n") if x])
                for k in views.keys(e, "department") for cell in views.grid(e, "department", k).values.ravel())
    assert lines == total


def test_room_view_overlays_dated_bookings_for_that_week_only(e):
    room = views.keys(e, "room")[0]
    mon = date(2026, 11, 16)
    free = 18                                         # 6-7 PM: bookable, never a timetable hour
    assert finder.book(e, room, mon, free, 1, "IT", "Seminar", "a@x")[0]
    assert "BOOKED 16 Nov: Seminar" in views.grid(e, "room", room, week_of=date(2026, 11, 18)).at[hour_label(free), "Mon"]
    assert views.grid(e, "room", room, week_of=date(2026, 11, 25)).at[hour_label(free), "Mon"] == ""
    assert views.grid(e, "room", room).at[hour_label(free), "Mon"] == ""


def test_export_has_one_sheet_per_key(e):
    book = pd.read_excel(io.BytesIO(views.export_excel(e, "teacher")), sheet_name=None, index_col=0)
    assert len(book) == len(views.keys(e, "teacher"))
    assert len(views.flat(e)) == db.q(e, "select count(*) n from placements")[0]["n"]


def test_empty_database_views_do_not_crash(engine):
    assert views.keys(engine, "student") == [] and views.keys(engine, "room") == []
    assert len(pd.read_excel(io.BytesIO(views.export_excel(engine, "student")), sheet_name=None)) == 1
