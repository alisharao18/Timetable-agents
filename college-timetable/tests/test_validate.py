import solver
import validate
from test_solver import tiny


def clean():
    p = tiny()
    return p, solver.board_rows(p, solver.construct(p))


def find(rows, key):
    return [r for r in rows if r["session_key"] == key]


def test_clean_result_audits_clean():
    p, rows = clean()
    assert validate.audit(p, rows) == []
    sc = validate.score(p, rows)
    assert sc["placed_hours"] == sc["total_hours"] == 10 and sc["own_room_share"] == 60.0   # 6 lecture hours in CR1, 4 lab hours elsewhere


def test_teacher_clash_is_caught():
    p, rows = clean()
    a, b = find(rows, "s1")[0], find(rows, "s2")[0]       # both taught by Rao
    b["day"], b["hour"], b["room_id"] = a["day"], a["hour"], 2
    errs = validate.audit(p, rows)
    assert any("Rao is in two places" in e for e in errs)
    assert any("3B IT has two classes" in e for e in errs)


def test_room_double_booking_and_oversize_are_caught():
    p, rows = clean()
    a, b = find(rows, "s1")[0], find(rows, "s4")[0]
    b["day"], b["hour"] = a["day"], a["hour"]
    assert any("CR1 is double-booked" in e for e in validate.audit(p, rows))
    p, rows = clean()
    find(rows, "s1")[0]["room_id"] = 2
    assert any("does not fit CR2 (40)" in e for e in validate.audit(p, rows))


def test_split_lab_wrong_lab_and_batch_clash_are_caught():
    p, rows = clean()
    lab = sorted(find(rows, "s7"), key=lambda r: r["hour"])
    lab[1]["room_id"] = 4
    errs = validate.audit(p, rows)
    assert any("is split" in e for e in errs)
    p, rows = clean()
    for r in find(rows, "s7"):
        r["room_id"] = 4
    assert any("needs a COMPUTER lab but L2 is PHYSICS" in e for e in validate.audit(p, rows))
    p, rows = clean()
    lec, lab = find(rows, "s1")[0], sorted(find(rows, "s7"), key=lambda r: r["hour"])[0]
    lec["day"], lec["hour"] = lab["day"], lab["hour"]
    assert any("while its division 3B IT also does" in e for e in validate.audit(p, rows))


def test_missing_lunch_and_fixed_rules_are_caught():
    p, rows = clean()
    lecs = [find(rows, f"s{i}")[0] for i in (1, 2, 4)]
    for r, h in zip(lecs, (12, 13, 14)):
        r["day"], r["hour"] = "Fri", h
    others = [r for r in rows if r not in lecs and r["day"] == "Fri"]
    for r in others:
        r["day"] = "Sat"
    errs = validate.audit(p, rows)
    assert any("3B IT-B1 has no free lunch hour on Fri" in e for e in errs)
    p, rows = clean()
    find(rows, "s1")[0]["hour"] = 19
    assert any("breaks a fixed rule" in e for e in validate.audit(p, rows))
    p, rows = clean()
    r = find(rows, "s1")[0]
    p.blocked.add((r["room_id"], r["day"], r["hour"]))
    assert any("already booked" in e for e in validate.audit(p, rows))
