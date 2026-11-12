"""Independent auditor and scorer. Trusts nothing: it re-derives every rule
from the placement rows alone and never uses the solver's own bookkeeping."""
from collections import defaultdict

from constants import DAYS, LUNCH_HOURS, TT_HOURS


def _sessions(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["session_key"]].append(r)
    return by


def audit(p, rows):
    """-> list of hard-rule violations in plain language ([] = clean)."""
    errs = []
    rooms = {r["id"]: r for r in p.rooms}
    sess = {p.key_of(s): s for s in p.sessions}
    code = lambda g: p.groups[g]["code"]
    seen_room, seen_teacher, seen_group = defaultdict(list), defaultdict(list), defaultdict(list)
    slot_of = defaultdict(set)

    for key, rs in _sessions(rows).items():
        s = sess.get(key)
        if s is None:
            errs.append(f"{key} is in the timetable but not in the teaching load.")
            continue
        rs = sorted(rs, key=lambda r: r["hour"])
        what = f"{code(s['group'])} {s['subject']}"
        if len(rs) != s["dur"]:
            errs.append(f"{what} should last {s['dur']} hour(s) but has {len(rs)}.")
        if len({r["day"] for r in rs}) > 1 or len({r["room_id"] for r in rs}) > 1 or \
                [r["hour"] for r in rs] != list(range(rs[0]["hour"], rs[0]["hour"] + len(rs))):
            errs.append(f"{what} is split: a lab must be consecutive hours in one room.")
        if any(r["group_id"] != s["group"] or r["teacher_id"] != s["teacher"] for r in rs):
            errs.append(f"{what} is recorded with the wrong class or teacher.")
        room = rooms.get(rs[0]["room_id"])
        d, h = rs[0]["day"], rs[0]["hour"]
        if room is None:
            errs.append(f"{what} is in a room that does not exist.")
        else:
            if room["capacity"] < s["size"]:
                errs.append(f"{what} ({s['size']} students) does not fit {room['name']} ({room['capacity']}).")
            want = "lab" if s["kind"] == "lab" else "classroom"
            if room["kind"] != want:
                errs.append(f"{what} is a {s['kind']} but {room['name']} is a {room['kind']}.")
            if s["kind"] == "lab" and s["lab_type"] and (room["lab_type"] or "").upper() != s["lab_type"].upper():
                errs.append(f"{what} needs a {s['lab_type']} lab but {room['name']} is {room['lab_type']}.")
        if not p.hard_ok(s, d, h):
            errs.append(f"{what} on {d} {h}:00 breaks a fixed rule (hours, campus days, "
                        "teacher availability or a must-have preference).")
        slot = (s["block"], s["meta"]) if s.get("block") else key   # an elective's options share time
        for r in rs:
            seen_room[r["room_id"], r["day"], r["hour"]].append(what)
            seen_teacher[s["teacher"], r["day"], r["hour"]].append(what)
            for g in p.groups_of(s):
                seen_group[g, r["day"], r["hour"]].append(what)
                slot_of[g, r["day"], r["hour"]].add(slot)
            if (r["room_id"], r["day"], r["hour"]) in p.blocked:
                errs.append(f"{what} on {r['day']} {r['hour']}:00 uses a room that is already booked.")

    for (rid, d, h), ws in seen_room.items():
        if len(ws) > 1:
            errs.append(f"{rooms[rid]['name']} is double-booked on {d} {h}:00: {', '.join(ws)}.")
    for (t, d, h), ws in seen_teacher.items():
        if len(ws) > 1:
            errs.append(f"{p.teachers[t]['name']} is in two places on {d} {h}:00: {', '.join(ws)}.")
    for (g, d, h), ws in seen_group.items():
        if len(slot_of[g, d, h]) > 1:
            errs.append(f"{code(g)} has two classes on {d} {h}:00: {', '.join(ws)}.")
        par = p.groups[g]["parent"]
        if par and (par, d, h) in seen_group:
            errs.append(f"{code(g)} has a class on {d} {h}:00 while its division {code(par)} also does.")

    for leaf in p.leaves():                          # lunch: one free hour in the window
        par = p.groups[leaf]["parent"]
        for d in DAYS:
            busy = [h for h in LUNCH_HOURS if (leaf, d, h) in seen_group or (par, d, h) in seen_group]
            if len(busy) == len(LUNCH_HOURS):
                errs.append(f"{code(leaf)} has no free lunch hour on {d}.")
    for t in p.teachers:
        for d in DAYS:
            if all((t, d, h) in seen_teacher for h in LUNCH_HOURS):
                errs.append(f"{p.teachers[t]['name']} has no free lunch hour on {d}.")

    # must-have max_days: never more days a week than the limit
    for root in {p.root(g) for g in p.groups}:       # the division and its batches together
        gp = p.pref_of(root)
        if gp.get("hard") and gp.get("max_days"):
            days = {d for (g, d, _) in seen_group if p.root(g) == root}
            if len(days) > gp["max_days"]:
                errs.append(f"{code(root)} comes {len(days)} days a week; it must be at most {gp['max_days']}.")
    for t, tp in p.tpref.items():
        if tp.get("hard") and tp.get("max_days"):
            days = {d for (x, d, _) in seen_teacher if x == t}
            if len(days) > tp["max_days"]:
                errs.append(f"{p.teachers[t]['name']} teaches {len(days)} days a week; "
                            f"must be at most {tp['max_days']}.")
    return errs


def _expand(p, rows):
    """Rows as seen by each class: an elective row also keeps every other
    class in its block busy. Copies carry `_copy` so they are not counted twice."""
    sess = {p.key_of(s): s for s in p.sessions}
    out = []
    for r in rows:
        out.append(r)
        s = sess.get(r.get("session_key"))
        if s and s.get("block"):
            out += [{**r, "group_id": g, "_copy": True} for g in p.groups_of(s) if g != r["group_id"]]
    return out


def lunch_plan(p, rows):
    """Who eats when. Each class that is on campus picks a free lunch hour;
    classes with a choice go to the emptier hour. -> ({(day, hour): students}, peak share)"""
    rows = _expand(p, rows)
    busy = defaultdict(set)
    for r in rows:
        busy[r["group_id"], r["day"]].add(r["hour"])
    load, worst = defaultdict(int), 0.0
    for d in DAYS:
        present, flexible = 0, []
        for leaf in p.leaves():
            par = p.groups[leaf]["parent"]
            hrs = busy.get((leaf, d), set()) | (busy.get((par, d), set()) if par else set())
            if not hrs:
                continue
            size = p.groups[leaf]["size"]
            present += size
            free = [h for h in LUNCH_HOURS if h not in hrs]
            if len(free) == 1:
                load[d, free[0]] += size
            elif free:
                flexible.append((size, free))
        for size, free in sorted(flexible, key=lambda x: -x[0]):
            load[d, min(free, key=lambda h: load[d, h])] += size
        if present:
            worst = max(worst, max(load[d, h] for h in LUNCH_HOURS) / present)
    return dict(load), worst


def score(p, rows):
    """Quality measures (hard rules aside). `penalty` is the single number
    the negotiating agents try to reduce."""
    sess = {p.key_of(s): s for s in p.sessions}
    rows = _expand(p, rows)
    starts = {}
    for r in rows:
        k = r["session_key"]
        if k not in starts or r["hour"] < starts[k]["hour"]:
            starts[k] = r
    dept_cost, dept_hours = defaultdict(float), defaultdict(int)
    happy = own = own_base = soft = 0
    for k, r in starts.items():
        s = sess[k]
        c = p.soft_cost(s, r["day"], r["hour"])
        dept = p.groups[s["group"]]["dept"]
        soft += c
        dept_cost[dept] += c
        dept_hours[dept] += s["dur"]
        happy += s["dur"] if c == 0 else 0
        mine = p.dept_rooms.get(dept)
        if mine:
            own_base += s["dur"]
            own += s["dur"] if r["room_id"] in mine else 0
    busy, t_days = defaultdict(set), defaultdict(set)
    lec_rooms = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if r.get("kind") == "lecture" and not r.get("block"):
            lec_rooms[r["group_id"]][r["room_id"]] += 1
    for r in rows:
        busy[r["group_id"], r["day"]].add(r["hour"])
        t_days[r["teacher_id"]].add(r["day"])
    gaps = days_over = student_days = 0
    for leaf in p.leaves():
        par = p.groups[leaf]["parent"]
        used = 0
        for d in DAYS:
            hrs = busy.get((leaf, d), set()) | (busy.get((par, d), set()) if par else set())
            if hrs:
                used += 1
                student_days += 1
                gaps += max(0, (max(hrs) - min(hrs) + 1) - len(hrs) - 1)   # one gap hour is lunch
                dept_cost[p.groups[leaf]["dept"]] += 0
        md = p.pref_of(leaf).get("max_days")
        if md and used > md:
            days_over += used - md
            dept_cost[p.groups[leaf]["dept"]] += 8 * (used - md)
    t_over = sum(max(0, len(t_days[t]) - p.tpref[t]["max_days"])
                 for t in p.teachers if p.tpref.get(t, {}).get("max_days"))
    total = sum(s["dur"] for s in sess.values()) or 1
    placed_hours = sum(1 for r in rows if not r.get("_copy"))
    per_dept = {p.depts.get(d, "No department"): round(10 - min(10, 10 * dept_cost[d] / max(1, dept_hours[d])), 2)
                for d in dept_hours}
    _, peak = lunch_plan(p, rows)
    return {
        "placed_hours": placed_hours, "total_hours": total,
        "preference": round(100 * happy / max(1, placed_hours), 1),     # % of hours with no complaint
        "fairness_min": min(per_dept.values()) if per_dept else 10.0,
        "per_dept": per_dept,
        "own_room_share": round(100 * own / own_base, 1) if own_base else None,
        "lunch_peak_share": round(100 * peak, 1),
        # share of lecture hours a division spends in its most-used classroom
        "home_room_share": round(100 * sum(max(v.values()) for v in lec_rooms.values())
                                 / max(1, sum(sum(v.values()) for v in lec_rooms.values())), 1),
        "gaps": round(gaps / max(1, student_days), 2),                  # idle hours per class-day
        "days_over": days_over, "teacher_days_over": t_over,
        "penalty": round(10 * soft + 80 * days_over + 50 * t_over + 3 * gaps
                         + 10000 * (total - placed_hours), 1),
    }
