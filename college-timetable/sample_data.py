"""A made-up but college-sized teaching load, for trying the generator
before the real load sheet exists. Uses the rooms already in the database
(from the workbook import); creates stand-in rooms if there are none."""
import random

import pandas as pd
from sqlalchemy import insert

import db
import templates

# department, programme, first sem-year, last year, divisions per year
PROGRAMMES = [
    ("First Year", "B.Tech", 1, 1, 14), ("First Year", "MBA Tech", 1, 1, 5),
    ("Computer", "B.Tech", 2, 4, 3), ("Computer", "BTI", 4, 6, 3), ("Computer", "MBA Tech", 2, 3, 2),
    ("IT", "B.Tech", 2, 4, 1), ("IT", "BTI", 4, 6, 1), ("IT", "MBA Tech", 2, 3, 1),
    ("Data Science", "B.Tech", 2, 4, 1), ("Data Science", "BTI", 4, 6, 1), ("Data Science", "MBA Tech", 2, 3, 1),
    ("AI", "B.Tech", 2, 4, 1), ("AI", "MBA Tech", 2, 3, 1),
    ("Mechanical", "B.Tech", 2, 4, 1), ("Mechanical", "BTI", 4, 6, 1),
    ("Mechatronics", "B.Tech", 2, 4, 1), ("Civil", "B.Tech", 2, 4, 1), ("EXTC", "B.Tech", 2, 4, 1),
    ("Technology Management", "MBA Tech", 4, 5, 4), ("MCA", "MCA", 1, 2, 1),
]
SUBJECTS = ["Mathematics", "Programming", "Systems", "Networks", "Design", "Analytics",
            "Management", "Electronics", "Mechanics", "Ethics"]
SHORT = {"First Year": "FY", "Computer": "CS", "Data Science": "DS", "Mechanical": "ME",
         "Mechatronics": "MX", "Civil": "CI", "Technology Management": "TM"}


def _standin_rooms(engine, n_class, n_lab):
    rows = [dict(name=f"CR-{i+1}", kind="classroom", room_type="STEP" if i % 3 else "FLAT",
                 capacity=[64, 68, 72, 82][i % 4], floor=1 + i % 8, owner_label="", lab_type=None)
            for i in range(n_class)]
    rows += [dict(name=f"LAB-{i+1}", kind="lab", room_type="LAB", capacity=30 + 2 * (i % 3),
                  floor=1 + i % 8, owner_label="", lab_type="COMPUTER") for i in range(n_lab)]
    with engine.begin() as c:
        c.execute(insert(db.rooms), rows)


def make_frames(engine, seed=0, scale=1.0):
    rng = random.Random(seed)
    groups, load, gprefs, teachers, tprefs, depts = [], [], [], [], [], []
    for dept, prog, y0, y1, n_div in PROGRAMMES:
        if dept not in depts:
            depts.append(dept)
        n_div = max(1, round(n_div * scale)) if scale >= 1 or rng.random() < scale * 1.5 else 0
        for year in range(y0, y1 + 1):
            for d in range(n_div):
                code = f"{2 * year - 1}{prog[0] if prog != 'BTI' else 'BTI'} {SHORT.get(dept, dept)}" + \
                       (f" {chr(65 + d)}" if n_div > 1 else "")
                if prog == "MBA Tech":
                    code = code.replace(f"{2*year-1}M", f"{2*year-1}M")
                if any(g[0] == code for g in groups):
                    code += f" ({prog})"
                size = rng.randint(54, 60)
                groups.append([code, dept, prog, year, size, ""])
                b1 = size // 2
                for b, bs in (("B1", b1), ("B2", size - b1)):
                    groups.append([f"{code}-{b}", dept, prog, year, bs, code])
                subs = rng.sample(SUBJECTS, 5)
                for s in subs:
                    load.append([code, f"{s} {year}", dept, 3, "lecture", ""])
                for s in subs[:3]:
                    for b in ("B1", "B2"):
                        load.append([f"{code}-{b}", f"{s} {year} Lab", dept, 2, "lab", "COMPUTER"])
                older = year >= 3
                days = "Mon,Tue,Wed,Thu,Fri" if older and rng.random() < 0.7 else ""
                gprefs.append([code, days, 4 if older and rng.random() < 0.4 else (5 if older else ""),
                               "no" if older else "yes", 8, 17 if older else 18, "no"])
    # teachers: enough per department that nobody exceeds ~14 hours a week
    by_dept = {}
    for row in load:
        by_dept.setdefault(row[2], []).append(row)
    for dept, rows in by_dept.items():
        hours = sum(r[3] for r in rows)
        names = [f"Prof. {SHORT.get(dept, dept)} {i + 1}" for i in range(max(2, -(-hours // 13)))]
        got = {n: 0 for n in names}
        for r in sorted(rows, key=lambda r: (r[1], r[0])):
            n = min(names, key=lambda n: (got[n], rng.random()))
            got[n] += r[3]
            r[2] = n
        for n in names:
            teachers.append([n, dept])
            roll = rng.random()
            tprefs.append([n, "Sat" if roll < 0.5 else (rng.choice(["Mon", "Fri"]) if roll < 0.6 else ""),
                           "", 5 if roll < 0.8 else 4, rng.choice(["morning", "afternoon", ""]), "no"])
    f = lambda name, rows: pd.DataFrame(rows, columns=templates.SHEETS[name][0])
    return {"Departments": f("Departments", [[d] for d in depts]),
            "DepartmentRooms": f("DepartmentRooms", []),
            "ClassGroups": f("ClassGroups", groups), "Teachers": f("Teachers", teachers),
            "TeachingLoad": f("TeachingLoad", load), "ClassPreferences": f("ClassPreferences", gprefs),
            "TeacherPreferences": f("TeacherPreferences", tprefs)}


def make_college(engine, seed=0, scale=1.0):
    """Fill the database with a sample college. -> problems (should be [])."""
    frames = make_frames(engine, seed, scale)
    if not db.q(engine, "select 1 from rooms limit 1"):
        n_div = sum(1 for p in frames["ClassGroups"]["parent_code"] if not p)
        _standin_rooms(engine, max(3, round(n_div * 0.42)), max(3, round(n_div * 0.5)))
    rooms = [r["name"] for r in db.q(engine, "select name from rooms")]
    clean, problems = templates.parse_frames(frames, rooms)
    if not problems:
        templates.save_clean(engine, clean, "sample data")
    return problems
