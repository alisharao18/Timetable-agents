import io
import pandas as pd
import db
import templates
import sample_data


def _book(frames):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        for k, v in frames.items():
            v.to_excel(xl, k, index=False)
    buf.seek(0)
    return buf


def _example():
    return pd.read_excel(io.BytesIO(templates.build_template_workbook()), sheet_name=None)


def _rooms(engine):
    from sqlalchemy import insert
    with engine.begin() as c:
        c.execute(insert(db.rooms).values(name="E.CR.501", kind="classroom", capacity=65))


def test_template_roundtrip(engine):
    _rooms(engine)
    assert templates.load_workbook_into_db(engine, io.BytesIO(templates.build_template_workbook())) == []
    assert db.q(engine, "select count(*) n from class_groups")[0]["n"] == 2
    batch = db.q(engine, "select parent_id from class_groups where code='3B IT-B1'")[0]
    assert batch["parent_id"] is not None
    p = db.q(engine, "select * from teacher_prefs")[0]
    assert p["unavailable_days"] == "Sat" and p["unavailable_hours"] == "Mon-8,Mon-9"
    out = pd.read_excel(io.BytesIO(templates.export_current(engine)), sheet_name=None)
    assert list(out["TeachingLoad"]["hours_per_week"]) == [3, 2]
    assert templates.load_workbook_into_db(engine, io.BytesIO(templates.export_current(engine))) == []


def test_bad_rows_are_named_and_nothing_is_written(engine):
    _rooms(engine)
    f = _example()
    f["TeachingLoad"].loc[0, "teacher"] = "Prof. Nobody"
    f["TeachingLoad"].loc[1, "hours_per_week"] = 3
    f["DepartmentRooms"].loc[0, "room"] = "E.CR.999"
    f["TeacherPreferences"].loc[0, "unavailable_hours"] = "Mon-25"
    problems = templates.load_workbook_into_db(engine, _book(f))
    text = " | ".join(problems)
    assert "TeachingLoad row 2: teacher 'Prof. Nobody'" in text
    assert "TeachingLoad row 3: a lab runs in 2-hour blocks" in text
    assert "DepartmentRooms row 2: room 'E.CR.999'" in text
    assert "'Mon-25' is not Day-Hour" in text
    assert db.q(engine, "select count(*) n from class_groups")[0]["n"] == 0
    assert db.q(engine, "select count(*) n from teachers")[0]["n"] == 0


def test_not_an_excel_file(engine):
    assert templates.load_workbook_into_db(engine, io.BytesIO(b"hello")) == [
        "That file could not be read as an Excel workbook."]


def test_sample_college_fits_the_rooms(engine):
    assert sample_data.make_college(engine, seed=1, scale=0.12) == []
    lec = db.q(engine, "select sum(hours) h from teaching_load where kind='lecture'")[0]["h"]
    lab = db.q(engine, "select sum(hours) h from teaching_load where kind='lab'")[0]["h"]
    n_cr = db.q(engine, "select count(*) n from rooms where kind='classroom'")[0]["n"]
    n_lab = db.q(engine, "select count(*) n from rooms where kind='lab'")[0]["n"]
    assert 0 < lec <= n_cr * 60 * 0.8 and 0 < lab <= n_lab * 60 * 0.8
    worst = db.q(engine, "select max(h) m from (select sum(hours) h from teaching_load group by teacher_id) x")[0]["m"]
    assert worst <= 18
