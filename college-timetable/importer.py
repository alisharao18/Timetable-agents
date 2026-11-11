"""Reads the two workbooks the committee already maintains, as they are.

  classrooms workbook: one tab per day, one column per room
  labs workbook:       one tab per floor, one block per lab

Every filled grid cell ends up in exactly one of three lists:
  weekly      a class code that repeats every week
  dated       a one-off booking with a readable date ("Booked on 26.10.2026")
  unresolved  text we refuse to guess at; the slot stays blocked until a
              person reads it
"""
import re
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime

import openpyxl
from sqlalchemy import delete, insert, select

import db
from constants import DAYS

DAY_TABS = {"MONDAY": "Mon", "TUESDAY": "Tue", "WEDNESDAY": "Wed",
            "THURSDAY": "Thu", "FRIDAY": "Fri", "SATURDAY": "Sat"}
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}

# keyword -> department, used only to GUESS a room's owner from sheet labels
DEPT_WORDS = [
    (r"\bFY\b|FIRST YEAR", "First Year"), (r"CIVIL|\bCI\b", "Civil"),
    (r"COMP|CSBS|CSCS|CSDS|CSEDS", "Computer"), (r"\bIT\b", "IT"),
    (r"\bDS\b|DATA SCIENCE", "Data Science"), (r"\bAI\b", "AI"),
    (r"\bME\b|MECHANICAL", "Mechanical"), (r"MXTC|MECHATRONICS", "Mechatronics"),
    (r"EXTC", "EXTC"), (r"\bMCA\b", "MCA"), (r"\bDIP", "Diploma"),
    (r"TECH MGMT|TECHNOLOGY MANAGEMENT|MBA ?TECH \d", "Technology Management"),
    (r"\bBSH\b|PHYSICS", "BSH")]


@dataclass
class ImportResult:
    rooms: list = field(default_factory=list)
    weekly: list = field(default_factory=list)       # room, day, hour, label
    dated: list = field(default_factory=list)        # room, date, hour, purpose
    unresolved: list = field(default_factory=list)   # room, day, hour, text
    notes: list = field(default_factory=list)


def guess_departments(label):
    up = (label or "").upper()
    return [d for pat, d in DEPT_WORDS if re.search(pat, up)]


def parse_hour(label):
    """'12 :: 1' -> 12, '1 :: 2' -> 13, '5:6' -> 17, '7am-8am' -> 7,
    '2pm-3pm' -> 14, '3 to 4' -> 15, a time object 05:06 -> 17."""
    if label is None:
        return None
    if hasattr(label, "hour") and not isinstance(label, str):
        n = label.hour
    else:
        m = re.match(r"\s*(\d{1,2})", str(label))
        if not m:
            return None
        n = int(m.group(1))
    if n < 7:
        n += 12
    return n if 7 <= n <= 18 else None


MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july",
               "august", "september", "october", "november", "december"]


def _month(word):
    w = word.lower()
    if w[:3] in MONTHS:
        return MONTHS[w[:3]]
    import difflib                                   # "Ocober" -> october
    hit = difflib.get_close_matches(w, MONTH_NAMES, n=1, cutoff=0.75)
    return MONTH_NAMES.index(hit[0]) + 1 if hit else None


def _dates_in(text, year):
    """All dates written in a cell. Returns (dates, complete): complete is
    False when the cell mentions a day number we could not turn into a date,
    so the caller can refuse to guess."""
    out, complete = [], True
    # 26.10.2026  30.9  7.10/14.10  (dots or slashes; a hyphen only with a year,
    # so time ranges like "9-4" or "8-12 pm" are never read as dates)
    for d, m, y in re.findall(r"(?<![\d.])(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?(?![\d])", text) + \
            re.findall(r"(?<!\d)(\d{1,2})-(\d{1,2})-(\d{2,4})(?!\d)", text):
        yy = int(y) if y else year
        yy += 2000 if yy < 100 else 0
        try:
            out.append(date(yy, int(m), int(d)))
        except ValueError:
            complete = False
    worded = 0
    for d, mon in re.findall(r"(\d{1,2})\s*(?:st|nd|rd|th)?\s*(?:of\s+)?([A-Za-z]{3,})", text):
        mm = _month(mon)
        if mm:
            worded += 1
            try:
                out.append(date(year, mm, int(d)))
            except ValueError:
                complete = False
    ordinals = len(re.findall(r"\b\d{1,2}(?:st|nd|rd|th)\b", text))
    if ordinals > worded:                            # "15th & 22nd of October"
        complete = False
    return sorted(set(out)), complete


def classify_cell(value, year, day=None):
    """-> (kind, dates) where kind is empty | code | dated | unresolved."""
    if value is None:
        return "empty", []
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text or text.startswith("=") or not re.search(r"[A-Za-z0-9]", text):
        return "empty", []
    if not re.search(r"book|reserv", text, re.I):
        return "code", []
    if re.search(r"\bevery\b", text, re.I):          # "booked on every saturday"
        return "code", []
    dates, complete = _dates_in(text, year)
    if not dates or not complete:
        return "unresolved", []
    if day and any(DAYS[d.weekday()] != day for d in dates if d.weekday() < 6):
        return "unresolved", []                      # date is not on this tab's day
    if any(d.weekday() == 6 for d in dates):
        return "unresolved", []
    return "dated", dates


def _purpose(text):
    m = re.search(r"\bfor\b\s*(.+)", text, re.I)
    return (m.group(1) if m else text).strip()[:300]


def _file_cell(res, room, day, hour, value, year):
    text = re.sub(r"\s+", " ", str(value)).strip() if value is not None else ""
    kind, dates = classify_cell(value, year, day)
    if kind == "code":
        res.weekly.append({"room": room, "day": day, "hour": hour, "label": text[:200]})
    elif kind == "dated":
        for d in dates:
            res.dated.append({"room": room, "date": d, "hour": hour,
                              "purpose": _purpose(text), "text": text})
    elif kind == "unresolved":
        res.unresolved.append({"room": room, "day": day, "hour": hour, "text": text})
    return kind


def _floor(text):
    m = re.search(r"(\d)", str(text or ""))
    return int(m.group(1)) if m else None


def _year_from(path, default=None):
    m = re.search(r"(20\d\d)", str(getattr(path, "name", path)))
    return int(m.group(1)) if m else (default or datetime.now().year)


def _open(path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return openpyxl.load_workbook(path)          # formulas stay as "=..." text


# --------------------------------------------------------------- classrooms
def parse_classrooms(path, year=None):
    year = year or _year_from(path)
    wb = _open(path)
    res = ImportResult()
    seen = {}
    for tab, day in DAY_TABS.items():
        if tab not in wb.sheetnames:
            res.notes.append(f"Tab '{tab}' is missing, so {day} was not imported.")
            continue
        ws = wb[tab]
        row_of = {str(ws.cell(r, 1).value or "").strip().lower(): r for r in range(1, 8)}
        r_name = row_of.get("room no.", 2)
        cols = [c for c in range(2, ws.max_column + 1)
                if re.match(r"\s*E\.", str(ws.cell(r_name, c).value or ""))]
        hours = {}
        for r in range(r_name + 1, min(ws.max_row, 60) + 1):
            if re.search(r"\d\s*:+\s*\d", str(ws.cell(r, 1).value or "")):
                hours[r] = parse_hour(ws.cell(r, 1).value)
        for c in cols:
            name = str(ws.cell(r_name, c).value).strip()
            if name not in seen:
                cap = ws.cell(row_of.get("capacity", 3), c).value
                owner = re.sub(r"\s+", " ", str(ws.cell(r_name - 1, c).value or "")).strip()
                seen[name] = {
                    "name": name, "kind": "classroom",
                    "room_type": str(ws.cell(row_of.get("room type", 4), c).value or "").strip().upper(),
                    "capacity": int(float(cap)) if cap else 0,
                    "floor": _floor(ws.cell(row_of.get("floor", 5), c).value),
                    "owner_label": owner, "lab_type": None}
            for r, h in hours.items():
                if h is not None:
                    _file_cell(res, name, day, h, ws.cell(r, c).value, year)
    res.rooms = list(seen.values())
    if "CR details" in wb.sheetnames:
        listed = {str(r[0].value).strip() for r in wb["CR details"].iter_rows(min_col=2, max_col=5)
                  for r in [[x for x in r if str(x.value or "").strip().startswith("E.")]] if r
                  }
        only_details = sorted(listed - set(seen))
        only_tabs = sorted(set(seen) - listed)
        if only_details or only_tabs:
            res.notes.append(
                "The 'CR details' tab and the day tabs list different rooms. The day tabs "
                f"were used. Only in CR details: {', '.join(only_details) or 'none'}. "
                f"Only in day tabs: {', '.join(only_tabs) or 'none'}.")
    return res


# --------------------------------------------------------------------- labs
def _lab_name(title, floor):
    t = re.sub(r"\s+", " ", title).strip()
    m = re.search(r"E\.?\s*(C[LC])\.?\s*(\d{3})", t, re.I)
    if m:
        return f"E.{m.group(1).upper()}.{m.group(2)}"
    m = re.match(r"COMP LAB\s*-?\s*(\d)\b", t, re.I)
    if m and floor:
        return f"E.CL.{floor}0{m.group(1)}"
    if re.match(r"Computer Cent", t, re.I) and floor:
        return f"E.CC.{floor}01"
    base = re.split(r"\(capacity", t, flags=re.I)[0]
    base = re.sub(r"^Hardware\s+Lab\s*-\s*", "", base, flags=re.I).strip(" -")
    return re.sub(r"\s*-\s*", " - ", base)[:40] if base else ""


def _lab_type(title):
    t = title.upper()
    if re.search(r"COMP LAB|COMPUTER CENT", t):
        return "COMPUTER"
    base = re.split(r"\(CAPACITY", t)[0]
    base = re.sub(r"HARDWARE\s+LAB\s*\d*\s*-?|\bLAB\b|\bLABS\b", " ", base)
    base = re.sub(r"\b\d+\b|-", " ", base)
    return re.sub(r"\s+", " ", base).strip(" -") or "GENERAL"


def parse_labs(path, year=None):
    year = year or _year_from(path)
    wb = _open(path)
    res = ImportResult()
    seen = {}
    for ws in wb.worksheets:
        if not re.search(r"floor", ws.title, re.I):
            continue
        floor = _floor(ws.title)
        merged = {}
        for rng in ws.merged_cells.ranges:           # a merged cell covers all its hours
            top = ws.cell(rng.min_row, rng.min_col).value
            for r in range(rng.min_row, rng.max_row + 1):
                for c in range(rng.min_col, rng.max_col + 1):
                    merged[(r, c)] = top
        val = lambda r, c: merged.get((r, c), ws.cell(r, c).value)
        for row in ws.iter_rows():
            for cell in row:
                if not (isinstance(cell.value, str) and cell.value.strip().lower() == "time"):
                    continue
                r0, c0 = cell.row, cell.column
                day_cols = {}
                for c in range(c0 + 1, c0 + 8):
                    d = str(ws.cell(r0, c).value or "").strip()[:3].title()
                    if d in DAYS:
                        day_cols[c] = d
                if len(day_cols) < 5:
                    continue                          # not a timetable block
                title = ""
                for c in range(c0, c0 + 7):           # title sits above, sometimes offset
                    t = val(r0 - 1, c) if r0 > 1 else None
                    if isinstance(t, str) and len(t.strip()) > 3:
                        title = t
                        break
                name = _lab_name(title, floor) if title else ""
                if not name:
                    name = f"Lab {ws.title} {cell.coordinate}"
                    res.notes.append(f"A lab block at {ws.title}!{cell.coordinate} has no "
                                     f"readable title; it was imported as '{name}'.")
                if name in seen:
                    res.notes.append(f"'{name}' appears twice ({ws.title}!{cell.coordinate}); "
                                     "the second block was skipped.")
                    continue
                cap = re.search(r"capacity\s*(\d+)", title, re.I)
                owner = title.split(")", 1)[1].strip(" -") if ")" in title else ""
                seen[name] = {"name": name, "kind": "lab", "room_type": "LAB",
                              "capacity": int(cap.group(1)) if cap else 0, "floor": floor,
                              "owner_label": re.sub(r"\s+", " ", owner)[:200],
                              "lab_type": _lab_type(title) if title else "GENERAL"}
                if not cap:
                    res.notes.append(f"No capacity found for '{name}'; set it by hand.")
                for r in range(r0 + 1, r0 + 14):
                    h = parse_hour(ws.cell(r, c0).value)
                    if h is None:
                        break
                    for c, d in day_cols.items():
                        _file_cell(res, name, d, h, val(r, c), year)
    res.rooms = list(seen.values())
    return res


# ------------------------------------------------------------------ into db
def load_into_db(engine, result, user="import"):
    """Upsert rooms, replace their sheet-sourced weekly cells, add dated
    bookings. Rooms not in `result` are left alone."""
    counts = dict(rooms=0, weekly=0, dated=0, dated_skipped=0, unresolved=0, weekly_skipped=0)
    with engine.begin() as c:
        have = {r.name: r.id for r in c.execute(select(db.rooms.c.id, db.rooms.c.name))}
        for r in result.rooms:
            if r["name"] in have:
                c.execute(db.rooms.update().where(db.rooms.c.id == have[r["name"]]).values(**r))
            else:
                have[r["name"]] = c.execute(insert(db.rooms).values(**r)).inserted_primary_key[0]
            counts["rooms"] += 1
        ids = [have[r["name"]] for r in result.rooms]
        dept_ids = {d.name: d.id for d in c.execute(select(db.departments.c.id, db.departments.c.name))}
        linked = {x.room_id for x in c.execute(select(db.department_rooms.c.room_id))}
        for r in result.rooms:                        # first guess at owners, from labels
            if have[r["name"]] in linked:
                continue
            for dname in guess_departments(r["owner_label"]):
                if dname not in dept_ids:
                    dept_ids[dname] = c.execute(
                        insert(db.departments).values(name=dname)).inserted_primary_key[0]
                c.execute(insert(db.department_rooms).values(
                    department_id=dept_ids[dname], room_id=have[r["name"]]))
        if ids:
            c.execute(delete(db.placements).where(
                db.placements.c.room_id.in_(ids),
                db.placements.c.source.in_(["sheet", "unresolved"])))
            c.execute(delete(db.unresolved).where(db.unresolved.c.room_id.in_(ids)))
        taken = {(p.room_id, p.day, p.hour) for p in c.execute(
            select(db.placements.c.room_id, db.placements.c.day, db.placements.c.hour))}
        rows = []
        for w in result.weekly:
            key = (have[w["room"]], w["day"], w["hour"])
            if key in taken:
                counts["weekly_skipped"] += 1
                continue
            taken.add(key)
            rows.append(dict(room_id=key[0], day=w["day"], hour=w["hour"],
                             label=w["label"], source="sheet"))
            counts["weekly"] += 1
        for u in result.unresolved:
            key = (have[u["room"]], u["day"], u["hour"])
            c.execute(insert(db.unresolved).values(room_id=key[0], day=u["day"],
                                                   hour=u["hour"], text=u["text"]))
            counts["unresolved"] += 1
            if key not in taken:
                taken.add(key)
                rows.append(dict(room_id=key[0], day=u["day"], hour=u["hour"],
                                 label="CHECK: " + u["text"][:190], source="unresolved"))
        if rows:
            c.execute(insert(db.placements), rows)
        booked = {(b.room_id, b.date, b.hour) for b in c.execute(
            select(db.bookings.c.room_id, db.bookings.c.date, db.bookings.c.hour))}
        new = []
        for d in result.dated:
            key = (have[d["room"]], d["date"], d["hour"])
            if key in booked:
                counts["dated_skipped"] += 1
                continue
            booked.add(key)
            new.append(dict(room_id=key[0], date=d["date"], hour=d["hour"],
                            purpose=d["purpose"], booked_by="sheet import",
                            department=None, created_at=datetime.now(),
                            booking_key=f"sheet-{key[0]}-{d['date']}"))
            counts["dated"] += 1
        if new:
            c.execute(insert(db.bookings), new)
    db.log(engine, user, "import", counts)
    return counts
