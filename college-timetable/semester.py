"""Which semester the timetable is for, and keeping past semesters.

Settings: academic year (e.g. 2026-27), term (Odd or Even), w.e.f. date and
campus. They label the PDFs, number the semesters (an Odd-term year-3 class
is Semester V, an Even-term one Semester VI) and give the importer its year.

Starting a new semester archives the published timetable first (it can be
downloaded later), then clears the generated and hand-edited classes and
the locks, so the committee starts clean.
"""
import io
import json
from datetime import date, datetime

import pandas as pd
from sqlalchemy import delete, insert

import db

DEFAULTS = dict(academic_year="2026-27", term="Odd", wef="", campus="Mumbai")
ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]


def get(engine) -> dict:
    return {k: db.get_setting(engine, f"sem_{k}", v) or v for k, v in DEFAULTS.items()}


def save(engine, academic_year=None, term=None, wef=None, campus=None):
    new = dict(academic_year=academic_year, term=term, wef=wef, campus=campus)
    if term is not None and term not in ("Odd", "Even"):
        raise ValueError("term must be Odd or Even")
    if academic_year is not None:
        y = str(academic_year).strip()
        if not (len(y) == 7 and y[:4].isdigit() and y[4] == "-" and y[5:].isdigit()):
            raise ValueError("academic year must look like 2026-27")
    for k, v in new.items():
        if v is not None:
            db.set_setting(engine, f"sem_{k}", v.isoformat() if isinstance(v, date) else v)


def label(sem: dict) -> str:
    return f"A.Y. {sem['academic_year']}, {sem['term']} semester"


def start_year(sem: dict) -> int:
    return int(str(sem["academic_year"])[:4])


def calendar_year(sem: dict) -> int:
    """The calendar year the term's dates fall in: an Odd term (July to
    December) is the first year of the A.Y., an Even term the second."""
    return start_year(sem) + (0 if sem["term"] == "Odd" else 1)


def sem_number(sem: dict, year: int) -> int:
    """Year of study -> semester number for this term."""
    return 2 * int(year) - (1 if sem["term"] == "Odd" else 0)


def roman(n: int) -> str:
    return ROMAN[n] if 0 < n < len(ROMAN) else str(n)


def archive(engine, user="admin") -> int | None:
    """Save the published timetable as a snapshot. -> archive id, or None
    if there is nothing to archive."""
    import views
    rows = views.flat(engine)
    rows = rows[rows["class_group"].notna()]
    if rows.empty:
        return None
    sem = get(engine)
    with engine.begin() as c:
        aid = c.execute(insert(db.archives).values(
            label=label(sem), created_at=datetime.now(), created_by=user,
            rows_json=rows.to_json(orient="records"))).inserted_primary_key[0]
    db.log(engine, user, "archive", f"{label(sem)}: {len(rows)} cells")
    return aid


def start_new(engine, academic_year, term, wef=None, user="admin") -> str:
    """Archive the current timetable, clear it and its locks, and switch the
    settings to the new semester. Rooms, bookings, classes, teachers and the
    teaching load are kept (upload the new load for the new semester)."""
    old = get(engine)
    aid = archive(engine, user)
    save(engine, academic_year=academic_year, term=term, wef=wef or "")
    with engine.begin() as c:
        c.execute(delete(db.placements).where(db.placements.c.source.in_(["generated", "manual"])))
        c.execute(delete(db.locks))
    db.log(engine, user, "new_semester", f"{label(old)} -> {label(get(engine))}")
    kept = f"The {label(old)} timetable was archived. " if aid else ""
    return kept + f"Now working on {label(get(engine))}. Upload its teaching load and generate."


def archives(engine) -> pd.DataFrame:
    return db.df(engine, "select id, label, created_at, created_by from archives order by id desc")


def archive_excel(engine, aid: int) -> bytes:
    r = db.q(engine, "select label, rows_json from archives where id=:i", i=aid)
    if not r:
        raise KeyError(aid)
    rows = pd.DataFrame(json.loads(r[0]["rows_json"]))
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        pd.DataFrame({"Timetable": [r[0]["label"]]}).to_excel(xl, sheet_name="About", index=False)
        rows.to_excel(xl, sheet_name="Timetable", index=False)
    return buf.getvalue()
