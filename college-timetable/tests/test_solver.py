import pytest
import db
import sample_data
import solver
import validate
from board import Board
from constants import DAYS, LUNCH_HOURS, TT_HOURS
from solver import Problem


def tiny():
    """One division (id 1) with two batches (2, 3), two teachers, 2 classrooms, 2 labs."""
    p = Problem()
    p.rooms = [dict(id=1, name="CR1", kind="classroom", capacity=70, lab_type=None, floor=1),
               dict(id=2, name="CR2", kind="classroom", capacity=40, lab_type=None, floor=1),
               dict(id=3, name="L1", kind="lab", capacity=30, lab_type="COMPUTER", floor=1),
               dict(id=4, name="L2", kind="lab", capacity=30, lab_type="PHYSICS", floor=1)]
    p.depts = {1: "IT"}
    p.groups = {1: dict(code="3B IT", size=60, parent=None, dept=1, year=2, programme="B.Tech"),
                2: dict(code="3B IT-B1", size=30, parent=1, dept=1, year=2, programme="B.Tech"),
                3: dict(code="3B IT-B2", size=30, parent=1, dept=1, year=2, programme="B.Tech")}
    p.teachers = {1: dict(name="Rao", dept=1), 2: dict(name="Shah", dept=1)}
    p.dept_rooms = {1: {1}}
    sid = 0

    def add(load, group, teacher, n, kind="lecture", lab_type=None):
        nonlocal sid
        for _ in range(n):
            sid += 1
            p.sessions.append(dict(id=sid, load=load, group=group, teacher=teacher, subject=f"S{load}",
                                   kind=kind, dur=2 if kind == "lab" else 1,
                                   size=p.groups[group]["size"], lab_type=lab_type))
    add(1, 1, 1, 3)
    add(2, 1, 2, 3)
    add(3, 2, 1, 1, "lab", "COMPUTER")
    add(4, 3, 2, 1, "lab", "COMPUTER")
    return p


def test_tiny_is_fully_placed_and_clean():
    p = tiny()
    assert solver.check_feasible(p) == []
    b = solver.construct(p)
    assert len(b.time) == len(p.sessions)
    rows = solver.board_rows(p, b)
    assert validate.audit(p, rows) == []
    lec = [r for r in rows if r["kind"] == "lecture"]
    assert {r["room_id"] for r in lec} == {1}            # 60 students only fit CR1, their own room
    assert {r["room_id"] for r in rows if r["kind"] == "lab"} == {3}   # COMPUTER labs only


def test_batch_clashes_with_its_division_but_not_its_sibling():
    p = tiny()
    b = Board(p)
    lecture, lab_b1, lab_b2 = p.sessions[0], p.sessions[6], p.sessions[7]
    assert b.try_place(lab_b1["id"], "Mon", 9)
    assert b.why_not(lecture, "Mon", 9) and b.why_not(lecture, "Mon", 10)   # division vs its batch
    assert b.why_not(lecture, "Mon", 11) is None
    assert b.conflicts(lab_b2, "Mon", 9) == set()                           # sibling batch is fine
    assert b.why_not(lab_b2, "Mon", 9) == "no suitable room is free then"   # ...but one COMPUTER lab
    b.p.rooms.append(dict(id=5, name="L3", kind="lab", capacity=30, lab_type="COMPUTER", floor=1))
    b._rooms.clear()
    assert b.try_place(lab_b2["id"], "Mon", 9)


def test_overloaded_teacher_is_named_before_solving():
    p = tiny()
    p.tpref[1] = dict(off={(d, h) for d in DAYS[1:] for h in TT_HOURS} |
                          {("Mon", h) for h in TT_HOURS[4:]}, max_days=None, preferred=None, hard=False)
    msgs = solver.check_feasible(p)
    assert any(m.startswith("Rao is assigned 5 hours a week but is only available for 3") for m in msgs)


def test_class_too_big_and_missing_lab_type_are_named():
    p = tiny()
    p.groups[1]["size"] = 90
    for s in p.sessions:
        s["size"] = p.groups[s["group"]]["size"]
    p.sessions[6]["lab_type"] = "CHEMISTRY"
    msgs = " ".join(solver.check_feasible(p))
    assert "3B IT (90 students) needs a classroom" in msgs
    assert "needs a CHEMISTRY lab" in msgs


def test_campus_days_and_teacher_unavailability_are_never_broken():
    p = tiny()
    p.gpref[1] = dict(days=["Mon", "Tue", "Wed"], max_days=None, saturday_ok=False,
                      earliest=None, latest=None, hard=False)
    p.tpref[2] = dict(off={("Mon", h) for h in TT_HOURS}, max_days=None, preferred=None, hard=False)
    b = solver.construct(p)
    assert len(b.time) == len(p.sessions)
    assert {d for d, _ in b.time.values()} <= {"Mon", "Tue", "Wed"}
    assert all(b.time[s["id"]][0] != "Mon" for s in p.sessions if s["teacher"] == 2)


def test_must_have_preferences_become_rules():
    p = tiny()
    p.gpref[1] = dict(days=[], max_days=None, saturday_ok=False, earliest=10, latest=16, hard=True)
    b = solver.construct(p)
    assert len(b.time) == len(p.sessions)
    for sid, (d, h) in b.time.items():
        assert d != "Sat" and h >= 10 and h + b.sess[sid]["dur"] <= 16


def test_unplaceable_session_is_reported_with_a_reason():
    p = tiny()
    p.tpref[1] = dict(off={(d, h) for d in DAYS for h in TT_HOURS if not (d == "Mon" and h in (8, 9, 10))},
                      max_days=None, preferred=None, hard=False)
    b = solver.construct(p)                                # Rao: 5 hours of work, 3 free hours
    left = solver.unplaced_reasons(p, b)
    assert left and all(u["reason"].startswith("No slot works.") for u in left)
    assert validate.audit(p, solver.board_rows(p, b)) == []


@pytest.fixture(scope="module")
def college(tmp_path_factory):
    e = db.get_engine(f"sqlite:///{tmp_path_factory.mktemp('c')/'c.db'}")
    db.init_db(e)
    assert sample_data.make_college(e, seed=1, scale=0.12) == []
    return e


def test_sample_college_all_placed_lunch_staggered(college):
    p, rows, unplaced, info = solver.generate(college, seed=0)
    assert unplaced == [] and info["placed"] == info["sessions"] >= 100
    assert validate.audit(p, rows) == []
    sc = validate.score(p, rows)
    assert sc["lunch_peak_share"] <= 55          # only 8 divisions here; a third is the ideal
    busy = {(r["group_id"], r["day"], r["hour"]) for r in rows}
    for leaf in p.leaves():
        par = p.groups[leaf]["parent"]
        for d in DAYS:
            assert any((leaf, d, h) not in busy and (par, d, h) not in busy for h in LUNCH_HOURS)


def test_improve_keeps_it_valid_and_not_worse(college):
    p = solver.load_problem(college)
    b = solver.construct(p)
    before = validate.score(p, solver.board_rows(p, b))["penalty"]
    solver.improve(p, b, seconds=10, workers=2)
    rows = solver.board_rows(p, b)
    assert validate.audit(p, rows) == [] and len(b.time) == len(p.sessions)
    assert validate.score(p, rows)["penalty"] <= before + 250   # lunch/gap trade-offs allowed, no collapse


def test_dated_bookings_block_the_room_in_generation(college):
    from datetime import date, timedelta
    from sqlalchemy import insert
    rid = db.q(college, "select id from rooms where kind='classroom' order by id limit 1")[0]["id"]
    nxt = date.today() + timedelta(days=(7 - date.today().weekday()) % 7 or 7)     # next Monday
    with college.begin() as c:
        c.execute(insert(db.bookings), [dict(room_id=rid, date=nxt, hour=h) for h in TT_HOURS])
    p, rows, _, _ = solver.generate(college)
    assert not [r for r in rows if r["room_id"] == rid and r["day"] == "Mon"]
    assert validate.audit(p, rows) == []
    db.run(college, "delete from bookings")


def test_without_ortools_improve_is_skipped_and_timetable_stays_valid(monkeypatch):
    """Windows/Anaconda sometimes cannot load the OR-Tools DLL. The app must
    still build a complete, valid timetable without the polish step."""
    monkeypatch.setattr(solver, "cp_model", None)
    p = tiny()
    b = solver.construct(p)
    before = solver.board_rows(p, b)
    assert solver.improve(p, b, seconds=5) == []
    rows = solver.board_rows(p, b)
    assert rows == before and validate.audit(p, rows) == []


def _days_per_leaf(p, rows):
    days = {}
    for leaf in p.leaves():
        par = p.groups[leaf]["parent"]
        days[leaf] = {r["day"] for r in rows if r["group_id"] in (leaf, par)}
    return days


def test_must_have_max_days_is_never_broken():
    """A class that must come at most 2 days: every batch comes on 2 days or fewer,
    through construction, polish and negotiation, and the audit agrees."""
    import agents
    p = tiny()
    p.gpref[1] = dict(days=[], max_days=2, saturday_ok=True, earliest=None, latest=None, hard=True)
    b = solver.construct(p)
    assert len(b.time) == len(p.sessions)
    rows = solver.board_rows(p, b)
    assert all(len(d) <= 2 for d in _days_per_leaf(p, rows).values())
    solver.improve(p, b, seconds=5)
    rows = solver.board_rows(p, b)
    rows, _ = agents.negotiate(p, rows, rounds=40, seed=1)
    assert all(len(d) <= 2 for d in _days_per_leaf(p, rows).values())
    assert validate.audit(p, rows) == []


def test_audit_reports_a_broken_must_have_max_days():
    p = tiny()
    b = solver.construct(p)
    rows = solver.board_rows(p, b)
    used = max(len(d) for d in _days_per_leaf(p, rows).values())
    p.gpref[1] = dict(days=[], max_days=used - 1, saturday_ok=True, earliest=None, latest=None, hard=True)
    assert any("days a week" in e for e in validate.audit(p, rows))


def test_too_many_hours_for_a_must_have_day_limit_is_named():
    p = tiny()
    p.gpref[1] = dict(days=[], max_days=1, saturday_ok=True, earliest=None, latest=None, hard=True)
    p.sessions += [dict(id=100 + i, load=9, group=1, teacher=1, subject="Extra", kind="lecture",
                        dur=1, size=60, lab_type=None) for i in range(4)]
    msgs = solver.check_feasible(p)
    assert any("3B IT-B1" in m and "in 1 day(s)" in m for m in msgs)
