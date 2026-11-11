from datetime import date
import pytest
import db
import importer
from conftest import DATA


@pytest.fixture(scope="module")
def cr():
    return importer.parse_classrooms(DATA / "classrooms.xlsx", year=2026)


@pytest.fixture(scope="module")
def labs():
    return importer.parse_labs(DATA / "labs.xlsx", year=2026)


def test_hour_labels():
    assert [importer.parse_hour(x) for x in
            ["7::8", "12 :: 1", "1 :: 2", "5:6", "6:7", "7am-8am", "2pm-3pm", "3 to 4", "12 to 1"]
            ] == [7, 12, 13, 17, 18, 7, 14, 15, 12]


def test_classify_cell():
    f = importer.classify_cell
    assert f("3B CS B", 2026)[0] == "code"
    assert f("   ", 2026)[0] == "empty"
    assert f("=IFERROR(__xludf.DUMMY())", 2026)[0] == "empty"
    assert f("Booked on 26.10.2026 for X", 2026, "Mon") == ("dated", [date(2026, 10, 26)])
    assert f("Booked on 31 Oct", 2026, "Sat") == ("dated", [date(2026, 10, 31)])
    assert f("Booked on 21.10.2026, 28.10.2026 for Y", 2026, "Wed")[1] == [
        date(2026, 10, 21), date(2026, 10, 28)]
    assert f("Booked full October", 2026)[0] == "unresolved"
    assert f("Booked on 26.10.2026", 2026, "Tue")[0] == "unresolved"   # 26 Oct is a Monday
    assert f("Engg. Ethics BTDS sem 1 booked on every saturday", 2026, "Sat")[0] == "code"


def test_classrooms(cr):
    names = {r["name"]: r for r in cr.rooms}
    assert len(names) == 42
    assert names["E.CR.101"]["capacity"] == 64 and names["E.CR.101"]["room_type"] == "STEP"
    assert names["E.CR.402"]["floor"] == 4
    wk = {(w["room"], w["day"], w["hour"]): w["label"] for w in cr.weekly}
    assert wk[("E.CR.101", "Mon", 8)] == "1B DS 2"
    assert wk[("E.CR.404", "Mon", 16)] == "OE-1 IGIS"
    assert any(d["room"] == "E.CR.108" and d["date"] == date(2026, 10, 26) and d["hour"] == 8
               for d in cr.dated)
    assert any("full October" in u["text"] and u["room"] == "E.CR.108" for u in cr.unresolved)
    assert any("different rooms" in n for n in cr.notes)


def test_every_classroom_cell_is_accounted_for(cr):
    import openpyxl, warnings
    warnings.simplefilter("ignore")
    wb = openpyxl.load_workbook(DATA / "classrooms.xlsx")
    filled = 0
    for tab in importer.DAY_TABS:
        for row in wb[tab].iter_rows(min_row=6, max_row=17, min_col=2, max_col=43):
            filled += sum(importer.classify_cell(c.value, 2026)[0] != "empty" for c in row)
    cells = ({(w["room"], w["day"], w["hour"]) for w in cr.weekly}
             | {(u["room"], u["day"], u["hour"]) for u in cr.unresolved}
             | {(d["room"], importer.DAYS[d["date"].weekday()], d["hour"]) for d in cr.dated})
    assert len(cells) == filled
    assert len(cr.weekly) + len(cr.unresolved) + len({(d["room"], d["hour"], d["text"]) for d in cr.dated}) == filled


def test_labs(labs):
    names = {r["name"]: r for r in labs.rooms}
    assert len(names) >= 45
    assert names["E.CL.501"]["capacity"] == 30 and names["E.CL.501"]["lab_type"] == "COMPUTER"
    assert names["E.CL.601"]["capacity"] == 30          # title sits one column to the right
    assert names["E.CL.101"]["floor"] == 1 and names["E.CC.201"]["capacity"] == 64
    assert all(r["capacity"] > 0 for r in labs.rooms)
    wk = {(w["room"], w["day"], w["hour"]): w["label"] for w in labs.weekly}
    assert wk[("E.CL.501", "Mon", 8)] == "3B IT"
    assert wk[("E.CL.401", "Mon", 9)] == "5B CS B"
    assert not any(w["label"].startswith("=") for w in labs.weekly)


def test_load_into_db_blocks_unresolved_and_is_repeatable(engine, cr):
    c1 = importer.load_into_db(engine, cr)
    assert c1["rooms"] == 42 and c1["weekly"] == len(cr.weekly)
    n1 = db.q(engine, "select count(*) n from placements")[0]["n"]
    b1 = db.q(engine, "select count(*) n from bookings")[0]["n"]
    blocked = db.q(engine, "select count(*) n from placements where source='unresolved'")[0]["n"]
    assert blocked >= 1
    importer.load_into_db(engine, cr)                    # importing again changes nothing
    assert db.q(engine, "select count(*) n from placements")[0]["n"] == n1
    assert db.q(engine, "select count(*) n from bookings")[0]["n"] == b1
    owners = db.q(engine, """select d.name from department_rooms x join departments d
                             on d.id=x.department_id join rooms r on r.id=x.room_id
                             where r.name='E.TR.401'""")
    assert [o["name"] for o in owners] == ["Civil"]
