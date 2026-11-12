"""The four timetables. All of them are just different ways of slicing the
same `placements` table, so they can never disagree with each other."""
import io
import re
from datetime import timedelta

import pandas as pd

import db
from constants import BOOK_HOURS, DAYS, TT_HOURS, hour_label

KINDS = {"student": "Class", "teacher": "Teacher", "department": "Department", "room": "Room"}


def frame(engine):
    """Every weekly placement with names attached."""
    f = db.df(engine, """
        select p.day, p.hour, p.subject, p.label, p.kind, p.source, p.session_key,
               r.name as room, g.code as class_group, pg.code as parent_group,
               t.name as teacher, d.name as department
        from placements p join rooms r on r.id = p.room_id
        left join class_groups g on g.id = p.group_id
        left join class_groups pg on pg.id = g.parent_id
        left join teachers t on t.id = p.teacher_id
        left join departments d on d.id = g.department_id""")
    if f.empty:
        f = pd.DataFrame(columns=["day", "hour", "subject", "label", "kind", "source", "session_key",
                                  "room", "class_group", "parent_group", "teacher", "department"])
    return f


def keys(engine, by, f=None):
    f = frame(engine) if f is None else f
    if by == "room":
        return [r["name"] for r in db.q(engine, "select name from rooms order by kind, name")]
    if by == "student":
        col = pd.concat([f["class_group"], f["parent_group"]])
    else:
        col = f[{"teacher": "teacher", "department": "department"}[by]]
    return sorted(col.dropna().unique())


def _blank(hours):
    return pd.DataFrame("", index=[hour_label(h) for h in hours], columns=DAYS)


def _put(table, day, hour, text):
    if day in table.columns and hour_label(hour) in table.index:
        old = table.at[hour_label(hour), day]
        table.at[hour_label(hour), day] = f"{old}\n{text}" if old else text


def grid(engine, by, key, week_of=None, f=None):
    """One timetable as a hours x days table of text.
    by: student | teacher | department | room. For rooms, pass `week_of`
    (any date in the week) to overlay that week's dated bookings."""
    f = frame(engine) if f is None else f
    table = _blank(BOOK_HOURS if by == "room" else TT_HOURS)
    if by == "student":
        sub = f[(f["class_group"] == key) | (f["parent_group"] == key)]
        mine = f[f["class_group"] == key]["parent_group"].dropna().unique()
        if len(mine):                                    # a batch also attends its division's lectures
            sub = pd.concat([sub, f[f["class_group"] == mine[0]]])
        for r in sub.itertuples():
            who = f"[{r.class_group}] " if r.class_group != key else ""
            _put(table, r.day, r.hour, f"{who}{r.subject}\n{r.teacher} · {r.room}")
    elif by == "teacher":
        for r in f[f["teacher"] == key].itertuples():
            _put(table, r.day, r.hour, f"{r.class_group} · {r.subject}\n{r.room}")
    elif by == "department":
        for r in f[f["department"] == key].sort_values("class_group").itertuples():
            _put(table, r.day, r.hour, f"{r.class_group}: {r.subject} ({r.room})")
    elif by == "room":
        for r in f[f["room"] == key].itertuples():
            _put(table, r.day, r.hour, r.label or "")
        if week_of is not None:
            monday = week_of - timedelta(days=week_of.weekday())
            for b in db.q(engine, """select b.date, b.hour, b.purpose, b.booked_by from bookings b
                                     join rooms r on r.id=b.room_id where r.name=:n""", n=key):
                d = b["date"] if not isinstance(b["date"], str) else pd.Timestamp(b["date"]).date()
                if monday <= d <= monday + timedelta(days=5):
                    _put(table, DAYS[d.weekday()], b["hour"],
                         f"BOOKED {d:%d %b}: {b['purpose'] or ''} ({b['booked_by'] or ''})")
    else:
        raise ValueError(f"unknown view '{by}'")
    return table


def flat(engine, f=None):
    """The whole timetable as one long list, for CSV download."""
    f = frame(engine) if f is None else f
    out = f[["day", "hour", "room", "class_group", "subject", "teacher", "department", "kind", "label", "source"]].copy()
    out["time"] = out["hour"].map(hour_label)
    out["d"] = out["day"].map(DAYS.index)
    return out.sort_values(["d", "hour", "room"]).drop(columns="d")


def export_excel(engine, by, week_of=None):
    """One workbook, one sheet per class / teacher / department / room."""
    f = frame(engine)
    buf, used = io.BytesIO(), set()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        names = keys(engine, by, f)
        if not names:
            pd.DataFrame({"Note": ["Nothing to show yet."]}).to_excel(xl, sheet_name="Empty", index=False)
        for k in names:
            name = re.sub(r"[\[\]:*?/\\]", "-", str(k))[:31] or "Sheet"
            while name.lower() in used:
                name = name[:28] + f"~{len(used) % 100}"
            used.add(name.lower())
            grid(engine, by, k, week_of, f).to_excel(xl, sheet_name=name)
            ws = xl.sheets[name]
            ws.column_dimensions["A"].width = 12
            for col in "BCDEFG":
                ws.column_dimensions[col].width = 34
            for row in ws.iter_rows(min_row=2):
                for c in row:
                    c.alignment = c.alignment.copy(wrap_text=True, vertical="top")
    return buf.getvalue()
