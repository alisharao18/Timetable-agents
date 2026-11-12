"""The input sheets the committee fills in, and the checks run on them.

One workbook, eight tabs. Nothing is written to the database unless every
tab passes its checks, so a half-loaded college can never exist.
"""
import io

import pandas as pd
from sqlalchemy import delete, insert, select

import db
from constants import DAYS, TT_HOURS

SHEETS = {
    "Departments": (["department"], [["Information Technology"]]),
    "DepartmentRooms": (["department", "room"], [["Information Technology", "E.CR.501"]]),
    "ClassGroups": (["code", "department", "programme", "year", "size", "parent_code"],
                    [["3B IT", "Information Technology", "B.Tech", 2, 60, ""],
                     ["3B IT-B1", "Information Technology", "B.Tech", 2, 30, "3B IT"]]),
    "Teachers": (["teacher", "department"], [["Prof. A. Rao", "Information Technology"],
                                             ["Prof. B. Shah", "Information Technology"]]),
    "TeachingLoad": (["class_group", "subject", "teacher", "hours_per_week", "type", "lab_type"],
                     [["3B IT", "Data Structures", "Prof. A. Rao", 3, "lecture", ""],
                      ["3B IT-B1", "Data Structures Lab", "Prof. A. Rao", 2, "lab", "COMPUTER"]]),
    "ClassPreferences": (["class_group", "days_on_campus", "max_days", "saturday_ok",
                          "earliest_start", "latest_finish", "must"],
                         [["3B IT", "Mon,Tue,Wed,Thu,Fri", 5, "no", 8, 18, "no"]]),
    "TeacherPreferences": (["teacher", "unavailable_days", "unavailable_hours", "max_days",
                            "preferred", "must"],
                           [["Prof. A. Rao", "Sat", "Mon-8,Mon-9", 5, "morning", "no"]]),
    "Electives": (["block", "class_groups", "subject", "teacher", "students", "hours_per_week",
                   "type", "lab_type"],
                  [["DE-I 3B IT", "3B IT", "Cloud Computing", "Prof. A. Rao", 30, 2, "lecture", ""],
                   ["DE-I 3B IT", "3B IT", "Blockchain", "Prof. B. Shah", 30, 2, "lecture", ""]]),
}
NOTES = [
    "How to fill this workbook",
    "Departments: one row per department.",
    "DepartmentRooms: the classrooms and labs each department is authorised to use. Room names must match the room list exactly.",
    "ClassGroups: one row per division, and one per lab batch. A batch names its division in parent_code.",
    "TeachingLoad: one row per subject per class group. type is lecture or lab. A lab runs as 2-hour blocks, so its hours must be even.",
    "ClassPreferences: days_on_campus are never broken. Blank means Monday to Saturday; list Sun as well for a class that also comes on Sundays (e.g. Mon,Tue,Wed,Thu,Fri,Sat,Sun). max_days, saturday_ok, earliest_start and latest_finish are preferences unless must = yes.",
    "TeacherPreferences: unavailable_days and unavailable_hours (Day-Hour, 24-hour clock, e.g. Mon-14) are never broken. max_days and preferred (morning or afternoon) are preferences unless must = yes.",
    "Electives (optional): every row with the same block runs at the same hours, each with its own teacher and room. "
    "class_groups lists every class whose students choose among them (one division for a department elective; many "
    "divisions across departments for an open elective); all of them are busy during the block. students is how many "
    "take that option. Rows in one block must have the same hours_per_week and type, and a teacher only once.",
    "Hours use the 24-hour clock: 8 means 8 AM, 13 means 1 PM. Classes run 8 to 18.",
]


def build_template_workbook(with_examples=True):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        pd.DataFrame({"Read me": NOTES}).to_excel(xl, sheet_name="ReadMe", index=False)
        for name, (cols, example) in SHEETS.items():
            pd.DataFrame(example if with_examples else [], columns=cols).to_excel(
                xl, sheet_name=name, index=False)
    return buf.getvalue()


def export_current(engine):
    """The data now in the database, in the same eight-tab layout."""
    frames = read_current(engine)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        pd.DataFrame({"Read me": NOTES}).to_excel(xl, sheet_name="ReadMe", index=False)
        for name, (cols, _) in SHEETS.items():
            frames.get(name, pd.DataFrame(columns=cols)).reindex(columns=cols).to_excel(
                xl, sheet_name=name, index=False)
    return buf.getvalue()


def read_current(engine):
    yn = lambda v: "yes" if v else "no"
    f = {}
    f["Departments"] = db.df(engine, "select name as department from departments order by name")
    f["DepartmentRooms"] = db.df(engine, """select d.name as department, r.name as room
        from department_rooms x join departments d on d.id=x.department_id
        join rooms r on r.id=x.room_id order by d.name, r.name""")
    f["ClassGroups"] = db.df(engine, """select g.code, d.name as department, g.programme, g.year,
        g.size, p.code as parent_code from class_groups g
        left join departments d on d.id=g.department_id
        left join class_groups p on p.id=g.parent_id order by g.code""")
    f["Teachers"] = db.df(engine, """select t.name as teacher, d.name as department from teachers t
        left join departments d on d.id=t.department_id order by t.name""")
    f["TeachingLoad"] = db.df(engine, """select g.code as class_group, l.subject, t.name as teacher,
        l.hours as hours_per_week, l.kind as type, l.lab_type from teaching_load l
        join class_groups g on g.id=l.group_id join teachers t on t.id=l.teacher_id
        where l.block is null order by g.code, l.subject""")
    el = db.q(engine, """select l.id, l.block, l.subject, t.name as teacher, l.students,
        l.hours as hours_per_week, l.kind as type, l.lab_type from teaching_load l
        join teachers t on t.id=l.teacher_id where l.block is not null order by l.block, l.subject""")
    mem = {}
    for r in db.q(engine, """select m.load_id, g.code from elective_members m
                             join class_groups g on g.id=m.group_id order by g.code"""):
        mem.setdefault(r["load_id"], []).append(r["code"])
    f["Electives"] = pd.DataFrame([{**{k: v for k, v in r.items() if k != "id"},
                                    "class_groups": ",".join(mem.get(r["id"], []))} for r in el],
                                  columns=SHEETS["Electives"][0])
    gp = db.df(engine, """select g.code as class_group, p.days_on_campus, p.max_days, p.saturday_ok,
        p.earliest as earliest_start, p.latest as latest_finish, p.hard as must
        from group_prefs p join class_groups g on g.id=p.group_id order by g.code""")
    tp = db.df(engine, """select t.name as teacher, p.unavailable_days, p.unavailable_hours,
        p.max_days, p.preferred, p.hard as must
        from teacher_prefs p join teachers t on t.id=p.teacher_id order by t.name""")
    if len(gp):
        gp["saturday_ok"], gp["must"] = gp["saturday_ok"].map(yn), gp["must"].map(yn)
    if len(tp):
        tp["must"] = tp["must"].map(yn)
    f["ClassPreferences"], f["TeacherPreferences"] = gp, tp
    return f


# ------------------------------------------------------------------ checks
def _s(v):
    return "" if v is None or (isinstance(v, float) and v != v) else str(v).strip()


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _yes(v):
    return _s(v).lower() in ("yes", "y", "true", "1", "must")


def _days(v, where, problems):
    out = []
    for part in [p for p in _s(v).replace(";", ",").split(",") if p.strip()]:
        d = part.strip()[:3].title()
        if d in DAYS:
            out.append(d)
        else:
            problems.append(f"{where}: '{part.strip()}' is not a day (use Mon, Tue, ... Sat, Sun).")
    return out


def _slots(v, where, problems):
    out = []
    for part in [p for p in _s(v).replace(";", ",").split(",") if p.strip()]:
        bits = part.strip().split("-")
        d, h = bits[0].strip()[:3].title(), _int(bits[1]) if len(bits) == 2 else None
        if d in DAYS and h in TT_HOURS:
            out.append(f"{d}-{h}")
        else:
            problems.append(f"{where}: '{part.strip()}' is not Day-Hour (e.g. Mon-14, hours 8 to 17).")
    return out


def parse_frames(frames, room_names):
    """Validate the eight tabs. -> (clean dict, problems). `clean` is only
    meaningful when problems is empty."""
    P, clean = [], {}
    for name, (cols, _) in SHEETS.items():
        if name not in frames:
            if name in ("Departments", "ClassGroups", "Teachers", "TeachingLoad"):
                P.append(f"The '{name}' tab is missing.")
            frames[name] = pd.DataFrame(columns=cols)
        df_ = frames[name].copy()
        df_.columns = [str(c).strip().lower() for c in df_.columns]
        miss = [c for c in cols if c not in df_.columns]
        if miss and len(df_):
            P.append(f"{name}: missing column(s) {', '.join(miss)}.")
        frames[name] = df_.reindex(columns=cols).dropna(how="all")
    if P:
        return clean, P
    row = lambda i: i + 2                                  # Excel row number

    depts = [_s(v) for v in frames["Departments"]["department"] if _s(v)]
    dset = {d.lower(): d for d in depts}
    if len(dset) != len(depts):
        P.append("Departments: a department is listed twice.")
    if not depts:
        P.append("Departments: add at least one department.")
    rooms = {r.lower(): r for r in room_names}

    def dept(v, where, required=True):
        s = _s(v)
        if not s and not required:
            return None
        if s.lower() not in dset:
            P.append(f"{where}: department '{s}' is not on the Departments tab.")
            return None
        return dset[s.lower()]

    clean["dept_rooms"] = []
    for i, r in frames["DepartmentRooms"].iterrows():
        w = f"DepartmentRooms row {row(i)}"
        d = dept(r["department"], w)
        if _s(r["room"]).lower() not in rooms:
            P.append(f"{w}: room '{_s(r['room'])}' is not in the room list.")
        elif d:
            clean["dept_rooms"].append((d, rooms[_s(r["room"]).lower()]))

    groups, clean["groups"] = {}, []
    for i, r in frames["ClassGroups"].iterrows():
        w = f"ClassGroups row {row(i)}"
        code, size = _s(r["code"]), _int(r["size"])
        if not code:
            P.append(f"{w}: code is empty.")
            continue
        if code.lower() in groups:
            P.append(f"{w}: class group '{code}' is listed twice.")
        if not size or size <= 0:
            P.append(f"{w}: size must be a positive number.")
        groups[code.lower()] = code
        clean["groups"].append(dict(code=code, department=dept(r["department"], w),
                                    programme=_s(r["programme"]), year=_int(r["year"]),
                                    size=size or 0, parent=_s(r["parent_code"])))
    for g in clean["groups"]:
        if g["parent"]:
            if g["parent"].lower() not in groups:
                P.append(f"ClassGroups: '{g['code']}' names parent '{g['parent']}', which is not listed.")
            elif g["parent"].lower() == g["code"].lower():
                P.append(f"ClassGroups: '{g['code']}' cannot be its own parent.")
            else:
                g["parent"] = groups[g["parent"].lower()]

    teachers, clean["teachers"] = {}, []
    for i, r in frames["Teachers"].iterrows():
        w = f"Teachers row {row(i)}"
        t = _s(r["teacher"])
        if not t:
            P.append(f"{w}: teacher name is empty.")
            continue
        if t.lower() in teachers:
            P.append(f"{w}: teacher '{t}' is listed twice.")
        teachers[t.lower()] = t
        clean["teachers"].append(dict(name=t, department=dept(r["department"], w, required=False)))

    clean["load"] = []
    for i, r in frames["TeachingLoad"].iterrows():
        w = f"TeachingLoad row {row(i)}"
        g, t, h, kind = _s(r["class_group"]), _s(r["teacher"]), _int(r["hours_per_week"]), _s(r["type"]).lower()
        ok = True
        if g.lower() not in groups:
            P.append(f"{w}: class group '{g}' is not on the ClassGroups tab.")
            ok = False
        if t.lower() not in teachers:
            P.append(f"{w}: teacher '{t}' is not on the Teachers tab.")
            ok = False
        if not _s(r["subject"]):
            P.append(f"{w}: subject is empty.")
            ok = False
        if kind not in ("lecture", "lab"):
            P.append(f"{w}: type must be lecture or lab, not '{kind}'.")
            ok = False
        if not h or h <= 0:
            P.append(f"{w}: hours_per_week must be a positive number.")
            ok = False
        elif kind == "lab" and h % 2:
            P.append(f"{w}: a lab runs in 2-hour blocks, so hours_per_week must be even (got {h}).")
            ok = False
        if ok:
            clean["load"].append(dict(group=groups[g.lower()], subject=_s(r["subject"]),
                                      teacher=teachers[t.lower()], hours=h, kind=kind,
                                      lab_type=(_s(r["lab_type"]).upper() or None) if kind == "lab" else None))
    if not clean["load"]:
        P.append("TeachingLoad: add at least one row.")

    clean["group_prefs"] = []
    for i, r in frames["ClassPreferences"].iterrows():
        w = f"ClassPreferences row {row(i)}"
        g = _s(r["class_group"])
        if g.lower() not in groups:
            P.append(f"{w}: class group '{g}' is not on the ClassGroups tab.")
            continue
        e, l = _int(r["earliest_start"]), _int(r["latest_finish"])
        if e is not None and e not in TT_HOURS:
            P.append(f"{w}: earliest_start must be between 8 and 17.")
        if l is not None and not 9 <= l <= 18:
            P.append(f"{w}: latest_finish must be between 9 and 18.")
        if e is not None and l is not None and l <= e:
            P.append(f"{w}: latest_finish must be after earliest_start.")
        md = _int(r["max_days"])
        if md is not None and not 1 <= md <= 6:
            P.append(f"{w}: max_days must be between 1 and 6.")
        clean["group_prefs"].append(dict(
            group=groups[g.lower()], days_on_campus=",".join(_days(r["days_on_campus"], w, P)),
            max_days=md, saturday_ok=_yes(r["saturday_ok"]) if _s(r["saturday_ok"]) else True,
            earliest=e, latest=l, hard=_yes(r["must"])))

    clean["teacher_prefs"] = []
    for i, r in frames["TeacherPreferences"].iterrows():
        w = f"TeacherPreferences row {row(i)}"
        t = _s(r["teacher"])
        if t.lower() not in teachers:
            P.append(f"{w}: teacher '{t}' is not on the Teachers tab.")
            continue
        pref = _s(r["preferred"]).lower()
        if pref not in ("", "morning", "afternoon"):
            P.append(f"{w}: preferred must be morning, afternoon or blank.")
        md = _int(r["max_days"])
        if md is not None and not 1 <= md <= 6:
            P.append(f"{w}: max_days must be between 1 and 6.")
        clean["teacher_prefs"].append(dict(
            teacher=teachers[t.lower()],
            unavailable_days=",".join(_days(r["unavailable_days"], w, P)),
            unavailable_hours=",".join(_slots(r["unavailable_hours"], w, P)),
            max_days=md, preferred=pref or None, hard=_yes(r["must"])))
    clean["electives"] = []
    blocks = {}
    for i, r in frames["Electives"].iterrows():
        w = f"Electives row {row(i)}"
        blk, t, h, kind = _s(r["block"]), _s(r["teacher"]), _int(r["hours_per_week"]), _s(r["type"]).lower()
        codes = [c.strip() for c in _s(r["class_groups"]).replace(";", ",").split(",") if c.strip()]
        ok = True
        if not blk:
            P.append(f"{w}: block is empty.")
            ok = False
        if not codes:
            P.append(f"{w}: list at least one class group.")
            ok = False
        for c in codes:
            if c.lower() not in groups:
                P.append(f"{w}: class group '{c}' is not on the ClassGroups tab.")
                ok = False
        if t.lower() not in teachers:
            P.append(f"{w}: teacher '{t}' is not on the Teachers tab.")
            ok = False
        if not _s(r["subject"]):
            P.append(f"{w}: subject is empty.")
            ok = False
        if kind not in ("lecture", "lab"):
            P.append(f"{w}: type must be lecture or lab, not '{kind}'.")
            ok = False
        if not h or h <= 0:
            P.append(f"{w}: hours_per_week must be a positive number.")
            ok = False
        elif kind == "lab" and h % 2:
            P.append(f"{w}: a lab runs in 2-hour blocks, so hours_per_week must be even (got {h}).")
            ok = False
        students = _int(r["students"])
        if _s(r["students"]) and (not students or students <= 0):
            P.append(f"{w}: students must be a positive number.")
            ok = False
        if not ok:
            continue
        first = blocks.setdefault(blk.lower(), dict(name=blk, hours=h, kind=kind, teachers=set(), row=w))
        if (h, kind) != (first["hours"], first["kind"]):
            P.append(f"{w}: every row of block '{blk}' must have the same hours_per_week and type "
                     f"as {first['row']} ({first['hours']} h, {first['kind']}).")
        if t.lower() in first["teachers"]:
            P.append(f"{w}: {t} already teaches another option of block '{blk}' at the same time.")
        first["teachers"].add(t.lower())
        clean["electives"].append(dict(
            block=first["name"], groups=sorted({groups[c.lower()] for c in codes}), subject=_s(r["subject"]),
            teacher=teachers[t.lower()], students=students, hours=h, kind=kind,
            lab_type=(_s(r["lab_type"]).upper() or None) if kind == "lab" else None))
    clean["departments"] = depts
    return clean, P


def save_clean(engine, clean, user="import"):
    """Replace the college's academic data in one transaction."""
    with engine.begin() as c:
        c.execute(delete(db.placements).where(db.placements.c.source.in_(["generated", "manual"])))
        for t in (db.elective_members, db.teaching_load, db.group_prefs, db.teacher_prefs):
            c.execute(delete(t))
        c.execute(delete(db.class_groups))
        c.execute(delete(db.teachers))
        did = {r.name: r.id for r in c.execute(select(db.departments.c.id, db.departments.c.name))}
        for d in clean["departments"]:
            if d not in did:
                did[d] = c.execute(insert(db.departments).values(name=d)).inserted_primary_key[0]
        if clean["dept_rooms"]:
            rid = {r.name: r.id for r in c.execute(select(db.rooms.c.id, db.rooms.c.name))}
            c.execute(delete(db.department_rooms))
            c.execute(insert(db.department_rooms), [
                dict(department_id=did[d], room_id=rid[r]) for d, r in sorted(set(clean["dept_rooms"]))])
        gid = {}
        for g in clean["groups"]:
            gid[g["code"]] = c.execute(insert(db.class_groups).values(
                code=g["code"], department_id=did.get(g["department"]), programme=g["programme"],
                year=g["year"], size=g["size"])).inserted_primary_key[0]
        for g in clean["groups"]:
            if g["parent"]:
                c.execute(db.class_groups.update().where(db.class_groups.c.id == gid[g["code"]])
                          .values(parent_id=gid[g["parent"]]))
        tid = {}
        for t in clean["teachers"]:
            tid[t["name"]] = c.execute(insert(db.teachers).values(
                name=t["name"], department_id=did.get(t["department"]))).inserted_primary_key[0]
        c.execute(insert(db.teaching_load), [
            dict(group_id=gid[l["group"]], subject=l["subject"], teacher_id=tid[l["teacher"]],
                 hours=l["hours"], kind=l["kind"], lab_type=l["lab_type"]) for l in clean["load"]])
        for e in clean.get("electives", []):
            lid = c.execute(insert(db.teaching_load).values(
                group_id=gid[e["groups"][0]], subject=e["subject"], teacher_id=tid[e["teacher"]],
                hours=e["hours"], kind=e["kind"], lab_type=e["lab_type"], block=e["block"],
                students=e["students"])).inserted_primary_key[0]
            c.execute(insert(db.elective_members), [dict(load_id=lid, group_id=gid[g]) for g in e["groups"]])
        if clean["group_prefs"]:
            c.execute(insert(db.group_prefs), [
                dict(group_id=gid[p["group"]], **{k: v for k, v in p.items() if k != "group"})
                for p in {p["group"]: p for p in clean["group_prefs"]}.values()])
        if clean["teacher_prefs"]:
            c.execute(insert(db.teacher_prefs), [
                dict(teacher_id=tid[p["teacher"]], **{k: v for k, v in p.items() if k != "teacher"})
                for p in {p["teacher"]: p for p in clean["teacher_prefs"]}.values()])
    db.log(engine, user, "load_inputs", f"{len(clean['groups'])} groups, {len(clean['load'])} load rows")


def load_workbook_into_db(engine, file, user="import"):
    """-> list of problems. Empty list means everything was saved."""
    try:
        frames = pd.read_excel(file, sheet_name=None)
    except Exception:
        return ["That file could not be read as an Excel workbook."]
    rooms = [r["name"] for r in db.q(engine, "select name from rooms")]
    clean, problems = parse_frames(frames, rooms)
    if not problems:
        save_clean(engine, clean, user)
    return problems
