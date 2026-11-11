import pytest
import db
import sample_data
import solver
import validate
import agents
from board import Board
from test_solver import tiny


@pytest.fixture(scope="module")
def start(tmp_path_factory):
    e = db.get_engine(f"sqlite:///{tmp_path_factory.mktemp('a')/'a.db'}")
    db.init_db(e)
    assert sample_data.make_college(e, seed=3, scale=0.12) == []
    with e.begin() as c:                                # give two departments their own rooms
        rooms = [r["id"] for r in db.q(e, "select id from rooms where kind='classroom' order by id")]
        depts = [d["id"] for d in db.q(e, "select distinct department_id id from class_groups")]
        from sqlalchemy import insert
        c.execute(insert(db.department_rooms), [
            dict(department_id=depts[i % len(depts)], room_id=r) for i, r in enumerate(rooms)])
    p = solver.load_problem(e)
    return p, solver.board_rows(p, solver.construct(p, seed=0))


def test_result_is_valid_and_not_worse(start):
    p, rows = start
    out, rep = agents.negotiate(p, [dict(r) for r in rows], rounds=40, seed=1)
    assert validate.audit(p, out) == []
    assert len(out) == len(rows)
    assert rep["after"]["penalty"] <= rep["before"]["penalty"]
    assert rep["accepted"] > 0 and rep["after"]["penalty"] < rep["before"]["penalty"]
    assert rep["history"][-1][1] <= rep["history"][0][1]


def test_same_seed_same_result(start):
    p, rows = start
    a, _ = agents.negotiate(p, [dict(r) for r in rows], rounds=15, seed=7)
    b, _ = agents.negotiate(p, [dict(r) for r in rows], rounds=15, seed=7)
    key = lambda r: (r["session_key"], r["hour"])
    assert sorted(a, key=key) == sorted(b, key=key)


def test_q_learning_also_runs(start):
    p, rows = start
    out, rep = agents.negotiate(p, [dict(r) for r in rows], rounds=15, seed=2, algo="q_learning")
    assert validate.audit(p, out) == [] and rep["algo"] == "q_learning"


def test_illegal_move_is_refused_and_leaves_everything_unchanged():
    p = tiny()
    b = solver.construct(p)
    led = agents.Ledger(p, b)
    before = (dict(b.time), dict(b.room))
    s1, s2 = p.sessions[0], p.sessions[3]               # two lectures of the same division
    d, h = b.time[s2["id"]]
    ok, _ = agents.try_move(b, led, [(s1["id"], d, h, None)])     # same students, same hour
    assert not ok and (b.time, b.room) == before
    ok, _ = agents.try_move(b, led, [(s1["id"], "Mon", 19, None)])  # after 6 PM
    assert not ok and (b.time, b.room) == before
    ok, _ = agents.try_move(b, led, [(s1["id"], *b.time[s1["id"]], 2)])   # 60 students, 40 seats
    assert not ok and (b.time, b.room) == before


def test_a_worse_move_is_refused():
    p = tiny()
    p.gpref[1] = dict(days=[], max_days=None, saturday_ok=False, earliest=None, latest=None, hard=False)
    b = solver.construct(p)
    led = agents.Ledger(p, b)
    sid = p.sessions[0]["id"]
    before = dict(b.time)
    free_sat = next(h for h in range(8, 12) if b.why_not(p.sessions[0], "Sat", h) is None)
    ok, gain = agents.try_move(b, led, [(sid, "Sat", free_sat, None)])   # class dislikes Saturdays
    assert not ok and gain < 0 and b.time == before


def test_unplaced_sessions_can_be_placed_by_negotiation():
    p = tiny()
    b = solver.construct(p)
    rows = [r for r in solver.board_rows(p, b) if r["session_key"] != "s1"]
    out, rep = agents.negotiate(p, rows, rounds=30, seed=0)
    assert any(r["session_key"] == "s1" for r in out)
    assert validate.audit(p, out) == []
