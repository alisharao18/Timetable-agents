"""Elective blocks: department electives inside one division, and open
electives across many divisions."""
import pandas as pd
import pytest
from sqlalchemy import insert

import db
import solver
import templates
import validate

ROOMS = [dict(name=f"CR{i}", kind="classroom", capacity=c, floor=1, lab_type=None) for i, c in
         enumerate([70, 70, 70, 40, 40, 40], 1)] + [dict(name="L1", kind="lab", capacity=35, floor=2,
                                                          lab_type="COMPUTER")]


def frames(electives):
    f = lambda name, rows: pd.DataFrame(rows, columns=templates.SHEETS[name][0])
    return {
        "Departments": f("Departments", [["IT"], ["CS"]]),
        "DepartmentRooms": f("DepartmentRooms", []),
        "ClassGroups": f("ClassGroups", [["5B IT", "IT", "B.Tech", 3, 60, ""], ["5B CS", "CS", "B.Tech", 3, 60, ""],
                                         ["5B CS-B1", "CS", "B.Tech", 3, 30, "5B CS"]]),
        "Teachers": f("Teachers", [[n, "IT"] for n in ("Rao", "Shah", "Iyer", "Khan", "Das")]),
        "TeachingLoad": f("TeachingLoad", [["5B IT", "Networks", "Rao", 3, "lecture", ""],
                                           ["5B CS", "Compilers", "Shah", 3, "lecture", ""],
                                           ["5B CS-B1", "Compilers Lab", "Shah", 2, "lab", "COMPUTER"]]),
        "ClassPreferences": f("ClassPreferences", []),
        "TeacherPreferences": f("TeacherPreferences", []),
        "Electives": f("Electives", electives),
    }


OE = [["OE-I", "5B IT,5B CS", "Design Thinking", "Iyer", 40, 2, "lecture", ""],
      ["OE-I", "5B IT,5B CS", "Financial Literacy", "Khan", 40, 2, "lecture", ""],
      ["OE-I", "5B IT,5B CS", "Psychology", "Das", 40, 2, "lecture", ""]]


@pytest.fixture
def college(engine):
    with engine.begin() as c:
        c.execute(insert(db.rooms), ROOMS)
    clean, problems = templates.parse_frames(frames(OE), [r["name"] for r in ROOMS])
    assert problems == []
    templates.save_clean(engine, clean)
    return engine


def test_open_elective_options_run_together_in_separate_rooms(college):
    p = solver.load_problem(college)
    block = [s for s in p.sessions if s.get("block")]
    assert len(block) == 6                               # 3 options x 2 hours
    b = solver.construct(p)
    assert len(b.time) == len(p.sessions)
    for meta in (0, 1):
        opts = [s for s in block if s["meta"] == meta]
        assert len({b.time[s["id"]] for s in opts}) == 1           # same hour
        assert len({b.room[s["id"]] for s in opts}) == 3           # three rooms
    days = {b.time[s["id"]][0] for s in block if s["n"] == 0 and s["subject"] == "Design Thinking"} | \
           {b.time[s["id"]][0] for s in block if s["n"] == 1 and s["subject"] == "Design Thinking"}
    assert len(days) == 2                                # the two hours fall on different days
    rows = solver.board_rows(p, b)
    assert validate.audit(p, rows) == []
    # nobody from 5B IT, 5B CS or its batch has another class during the block
    times = {b.time[s["id"]] for s in block}
    for s in p.sessions:
        if not s.get("block"):
            d, h = b.time[s["id"]]
            assert not any((d, h + k) in times for k in range(s["dur"]))


def test_audit_catches_a_class_during_its_elective(college):
    p = solver.load_problem(college)
    b = solver.construct(p)
    rows = solver.board_rows(p, b)
    blk = next(r for r in rows if r.get("block"))
    lec = next(r for r in rows if r["subject"] == "Networks")
    lec["day"], lec["hour"] = blk["day"], blk["hour"]
    assert any("two classes" in e for e in validate.audit(p, rows))


def test_agents_and_polish_never_move_electives(college):
    import agents
    p = solver.load_problem(college)
    b = solver.construct(p)
    before = {s["id"]: (b.time[s["id"]], b.room[s["id"]]) for s in p.sessions if s.get("block")}
    solver.improve(p, b, seconds=3)
    rows, _ = agents.negotiate(p, solver.board_rows(p, b), rounds=30, seed=2)
    after = {r["session_key"]: (r["day"], r["hour"], r["room_id"]) for r in rows if r.get("block")}
    for s in p.sessions:
        if s.get("block"):
            (d, h), room = before[s["id"]]
            assert after[s["key"]] == (d, h, room)
    assert validate.audit(p, rows) == []


@pytest.mark.parametrize("bad, expect", [
    ([OE[0], [*OE[1][:5], 3, "lecture", ""]], "same hours_per_week and type"),
    ([OE[0], [*OE[1][:3], "Iyer", 40, 2, "lecture", ""]], "already teaches another option"),
    ([["OE-I", "5B IT,9X", "Design Thinking", "Iyer", 40, 2, "lecture", ""]], "'9X' is not on the ClassGroups tab"),
    ([["OE-I", "5B IT", "Robotics", "Iyer", 30, 3, "lab", "COMPUTER"]], "must be even"),
])
def test_electives_tab_problems_are_named(bad, expect):
    _, problems = templates.parse_frames(frames(bad), [r["name"] for r in ROOMS])
    assert any(expect in m for m in problems), problems


def test_electives_survive_export_and_reupload(college):
    out = templates.read_current(college)["Electives"]
    assert len(out) == 3 and set(out["class_groups"]) == {"5B CS,5B IT"}


def test_every_member_class_sees_the_block_in_its_timetable(college):
    import views
    p = solver.load_problem(college)
    b = solver.construct(p)
    db.replace_placements(college, solver.board_rows(p, b), "generated")
    for code in ("5B IT", "5B CS", "5B CS-B1"):
        cells = "\n".join(views.grid(college, "student", code).values.ravel())
        assert cells.count("OE-I:") == 6, code             # 3 options x 2 hours
