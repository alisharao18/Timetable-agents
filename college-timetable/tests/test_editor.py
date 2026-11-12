"""Hand edits and locks."""
import pytest
from sqlalchemy import insert

import db
import editor
import solver
import templates
import validate
from test_electives import OE, ROOMS, frames


@pytest.fixture
def published(engine):
    with engine.begin() as c:
        c.execute(insert(db.rooms), ROOMS)
    clean, problems = templates.parse_frames(frames(OE), [r["name"] for r in ROOMS])
    assert problems == []
    templates.save_clean(engine, clean)
    p = solver.load_problem(engine)
    b = solver.construct(p)
    db.replace_placements(engine, solver.board_rows(p, b), "generated")
    return engine


def _rows(engine):
    return db.q(engine, "select * from placements")


def _first(engine, subject):
    p, b = editor.current(engine)
    return next(s for s in p.sessions if s["subject"] == subject and s["n"] == 0), p, b


def test_move_to_a_legal_slot_is_saved_and_stays_clean(published):
    s, p, b = _first(published, "Networks")
    opts = [o for o in editor.options(p, b, s["key"]) if not o["current"]]
    assert opts and all(o["rooms"] for o in opts)
    o = opts[-1]
    ok, msg = editor.move(published, s["key"], o["day"], o["hour"], o["rooms"][0], "test")
    assert ok, msg
    p, b = editor.current(published)
    assert b.time[s["id"]] == (o["day"], o["hour"])
    assert validate.audit(p, [r for r in _rows(published) if r["session_key"]]) == []
    assert {r["source"] for r in _rows(published) if r["session_key"] == s["key"]} == {"manual"}


def test_move_into_a_clash_is_refused_with_the_reason(published):
    s, p, b = _first(published, "Networks")
    other = next(x for x in p.sessions if x["subject"] == "Networks" and x["n"] == 1)
    d, h = b.time[other["id"]]
    before = _rows(published)
    ok, msg = editor.move(published, s["key"], d, h)
    assert not ok and msg.startswith("Not moved:")
    assert _rows(published) == before


def test_elective_sessions_cannot_be_moved_by_hand(published):
    s, p, b = _first(published, "Design Thinking")
    ok, msg = editor.move(published, s["key"], "Fri", 9)
    assert not ok and "elective" in msg


def test_swap_two_lectures(published):
    p, b = editor.current(published)
    a = next(x for x in p.sessions if x["subject"] == "Networks" and x["n"] == 0)
    c = next(x for x in p.sessions if x["subject"] == "Compilers" and x["n"] == 0)
    ta, tc = b.time[a["id"]], b.time[c["id"]]
    ok, msg = editor.swap(published, a["key"], c["key"], "test")
    if ok:
        p, b = editor.current(published)
        assert b.time[a["id"]] == tc and b.time[c["id"]] == ta
        assert validate.audit(p, [r for r in _rows(published) if r["session_key"]]) == []
    else:
        assert msg.startswith("Not swapped")


def test_locked_class_survives_regeneration(published):
    s, p, b = _first(published, "Networks")
    opts = [o for o in editor.options(p, b, s["key"]) if not o["current"]]
    o = opts[len(opts) // 2]
    assert editor.move(published, s["key"], o["day"], o["hour"], o["rooms"][0])[0]
    ok, _ = editor.lock(published, [s["key"]], "test")
    assert ok
    assert len(editor.all_locks(published)) == 1
    for seed in (0, 1, 2):                                    # generate again, several ways
        p2 = solver.load_problem(published)
        b2 = solver.construct(p2, seed=seed)
        sid = p2.by_key[s["key"]]
        room = next(r["id"] for r in p2.rooms if r["name"] == o["rooms"][0])
        assert (b2.time[sid], b2.room[sid]) == ((o["day"], o["hour"]), room)
        assert sid in b2.fixed
        assert validate.audit(p2, solver.board_rows(p2, b2)) == []
    assert not editor.move(published, s["key"], "Mon", 8)[0]          # locked: must unlock first
    assert editor.unlock(published, [s["key"]])[0]
    assert editor.all_locks(published).empty


def test_lock_that_breaks_a_rule_is_reported_and_dropped(published):
    s, p, b = _first(published, "Networks")
    editor.lock(published, [s["key"]])
    d, h = b.time[s["id"]]
    tid = s["teacher"]
    with published.begin() as c:                              # the teacher becomes unavailable then
        c.execute(insert(db.teacher_prefs).values(teacher_id=tid, unavailable_hours=f"{d}-{h}"))
    p2 = solver.load_problem(published)
    b2 = solver.construct(p2)
    assert any("was not kept" in n for n in p2.lock_notes)
    assert b2.time.get(p2.by_key[s["key"]]) != (d, h)
    assert validate.audit(p2, solver.board_rows(p2, b2)) == []
