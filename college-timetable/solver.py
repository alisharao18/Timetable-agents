"""Builds the weekly timetable.

  load_problem    read everything once into plain Python (no DB after this)
  check_feasible  name anything impossible BEFORE solving
  construct       places every session (time and room), most constrained first
  improve         CP-SAT re-times one department at a time to raise quality
  to_rows         rows ready for db.replace_placements

A "session" is one lecture hour, or one 2-hour lab block.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

try:
    from ortools.sat.python import cp_model
    CP_SAT_ERROR = None
except Exception as e:      # e.g. Windows "DLL load failed" inside Anaconda
    cp_model = None
    CP_SAT_ERROR = f"{type(e).__name__}: {e}"

import db
from constants import DAYS, LUNCH_HOURS, TT_HOURS

LAST_END = TT_HOURS[-1] + 1          # 18: classes end by 6 PM


@dataclass
class Problem:
    rooms: list = field(default_factory=list)        # dicts: id,name,kind,capacity,lab_type,floor
    groups: dict = field(default_factory=dict)       # id -> code,size,parent,dept,year
    teachers: dict = field(default_factory=dict)     # id -> name,dept
    depts: dict = field(default_factory=dict)        # id -> name
    sessions: list = field(default_factory=list)     # dicts: id,group,teacher,subject,kind,dur,size,lab_type
    gpref: dict = field(default_factory=dict)        # division id -> days,max_days,saturday_ok,earliest,latest,hard
    tpref: dict = field(default_factory=dict)        # teacher id -> off,max_days,preferred,hard
    dept_rooms: dict = field(default_factory=dict)   # dept id -> {room id}
    blocked: set = field(default_factory=set)        # {(room id, day, hour)}
    canteen_limit: int = 0                           # 0 = no limit set

    # ---- helpers used by solver, validator and agents alike
    def root(self, g):
        p = self.groups[g]["parent"]
        return p if p else g

    def pref_of(self, g):
        return self.gpref.get(self.root(g), {})

    def group_days(self, g):
        days = self.pref_of(g).get("days")
        return list(days) if days else list(DAYS)

    def children(self):
        ch = defaultdict(list)
        for g, v in self.groups.items():
            if v["parent"]:
                ch[v["parent"]].append(g)
        return ch

    def leaves(self):
        """Groups that stand for actual students: batches, or divisions with no batches."""
        ch = self.children()
        return [g for g in self.groups if g not in ch]

    def teacher_free(self, t, day, hour):
        return (day, hour) not in self.tpref.get(t, {}).get("off", ())

    def hard_ok(self, s, day, h):
        """Every rule about WHEN session s may start that is never broken."""
        if h < TT_HOURS[0] or h + s["dur"] > LAST_END:
            return False
        if day not in self.group_days(s["group"]):
            return False
        if not all(self.teacher_free(s["teacher"], day, h + k) for k in range(s["dur"])):
            return False
        gp = self.pref_of(s["group"])
        if gp.get("hard"):
            if day == "Sat" and gp.get("saturday_ok") is False:
                return False
            if gp.get("earliest") and h < gp["earliest"]:
                return False
            if gp.get("latest") and h + s["dur"] > gp["latest"]:
                return False
        tp = self.tpref.get(s["teacher"], {})
        if tp.get("hard") and tp.get("preferred"):
            if tp["preferred"] == "morning" and h + s["dur"] > 13:
                return False
            if tp["preferred"] == "afternoon" and h < 12:
                return False
        return True

    def soft_cost(self, s, day, h):
        """Penalty points for placing s here (0 = everyone is happy)."""
        cost = 0
        gp, tp = self.pref_of(s["group"]), self.tpref.get(s["teacher"], {})
        if day == "Sat" and gp.get("saturday_ok") is False:
            cost += 6 * s["dur"]
        if gp.get("earliest") and h < gp["earliest"]:
            cost += 3
        if gp.get("latest") and h + s["dur"] > gp["latest"]:
            cost += 3 * (h + s["dur"] - gp["latest"])
        if tp.get("preferred") == "morning" and h + s["dur"] > 13:
            cost += 2
        if tp.get("preferred") == "afternoon" and h < 12:
            cost += 2
        if h + s["dur"] > 17:                         # the 5-6 PM hour is nobody's favourite
            cost += 1
        return cost

    def rooms_for(self, s):
        if s["kind"] == "lab":
            return [r for r in self.rooms if r["kind"] == "lab" and r["capacity"] >= s["size"]
                    and (not s["lab_type"] or (r["lab_type"] or "").upper() == s["lab_type"].upper())]
        return [r for r in self.rooms if r["kind"] == "classroom" and r["capacity"] >= s["size"]]


def load_problem(engine, today=None):
    p = Problem()
    p.rooms = db.q(engine, "select id,name,kind,capacity,lab_type,floor from rooms order by id")
    p.depts = {r["id"]: r["name"] for r in db.q(engine, "select id,name from departments")}
    for r in db.q(engine, "select * from class_groups"):
        p.groups[r["id"]] = dict(code=r["code"], size=r["size"], parent=r["parent_id"],
                                 dept=r["department_id"], year=r["year"], programme=r["programme"])
    for r in db.q(engine, "select * from teachers"):
        p.teachers[r["id"]] = dict(name=r["name"], dept=r["department_id"])
    for r in db.q(engine, "select * from group_prefs"):
        days = [d for d in (r["days_on_campus"] or "").split(",") if d in DAYS]
        p.gpref[r["group_id"]] = dict(days=days, max_days=r["max_days"],
                                      saturday_ok=None if r["saturday_ok"] is None else bool(r["saturday_ok"]),
                                      earliest=r["earliest"], latest=r["latest"], hard=bool(r["hard"]))
    for r in db.q(engine, "select * from teacher_prefs"):
        off = {(d, h) for d in (r["unavailable_days"] or "").split(",") if d in DAYS for h in TT_HOURS}
        for part in (r["unavailable_hours"] or "").split(","):
            if "-" in part:
                d, h = part.split("-")
                off.add((d, int(h)))
        p.tpref[r["teacher_id"]] = dict(off=off, max_days=r["max_days"],
                                        preferred=r["preferred"], hard=bool(r["hard"]))
    for r in db.q(engine, "select department_id, room_id from department_rooms"):
        p.dept_rooms.setdefault(r["department_id"], set()).add(r["room_id"])
    sid = 0
    for r in db.q(engine, "select * from teaching_load order by id"):
        dur = 2 if r["kind"] == "lab" else 1
        for _ in range(r["hours"] // dur):
            sid += 1
            p.sessions.append(dict(id=sid, load=r["id"], group=r["group_id"], teacher=r["teacher_id"],
                                   subject=r["subject"], kind=r["kind"], dur=dur,
                                   size=p.groups[r["group_id"]]["size"], lab_type=r["lab_type"]))
    for r in db.q(engine, "select room_id, day, hour from placements where source='unresolved'"):
        p.blocked.add((r["room_id"], r["day"], r["hour"]))
    today = today or date.today()
    for r in db.q(engine, "select room_id, date, hour from bookings"):
        d = r["date"] if isinstance(r["date"], date) else date.fromisoformat(str(r["date"])[:10])
        if d >= today and d.weekday() < 6:
            p.blocked.add((r["room_id"], DAYS[d.weekday()], r["hour"]))
    p.canteen_limit = int(float(db.get_setting(engine, "canteen_limit", 0) or 0))
    return p


# ------------------------------------------------------------ feasibility
def check_feasible(p):
    """Plain-language list of things that make a full timetable impossible."""
    out = []
    if not p.sessions:
        return ["There is no teaching load to schedule."]
    t_hours, fam_hours = defaultdict(int), defaultdict(int)
    for s in p.sessions:
        t_hours[s["teacher"]] += s["dur"]
        fam_hours[s["group"]] += s["dur"]
        rooms = p.rooms_for(s)
        if not rooms:
            g = p.groups[s["group"]]
            what = (f"a {s['lab_type'] or ''} lab".replace("  ", " ") if s["kind"] == "lab" else "a classroom")
            msg = f"{g['code']} ({s['size']} students) needs {what} for {s['subject']}, but none is big enough or of that type."
            if msg not in out:
                out.append(msg)
        if not any(p.hard_ok(s, d, h) for d in DAYS for h in TT_HOURS):
            msg = (f"{s['subject']} for {p.groups[s['group']]['code']} has no possible time: "
                   f"{p.teachers[s['teacher']]['name']} is never free when the class is on campus.")
            if msg not in out:
                out.append(msg)
    for t, need in t_hours.items():
        free = sum(p.teacher_free(t, d, h) for d in DAYS for h in TT_HOURS)
        days = len({d for d in DAYS for h in TT_HOURS if p.teacher_free(t, d, h)})
        if need > free - days:                      # one lunch hour a day
            out.append(f"{p.teachers[t]['name']} is assigned {need} hours a week but is only "
                       f"available for {max(free - days, 0)} (after a daily lunch hour).")
    for g in p.leaves():
        need = fam_hours[g] + (fam_hours[p.groups[g]["parent"]] if p.groups[g]["parent"] else 0)
        have = len(p.group_days(g)) * (len(TT_HOURS) - 1)
        if need > have:
            out.append(f"{p.groups[g]['code']} has {need} hours of classes a week but only "
                       f"{have} hours on campus (after a daily lunch hour).")
    for kind, label in (("lecture", "classroom"), ("lab", "lab")):
        need = sum(s["dur"] for s in p.sessions if s["kind"] == kind)
        rooms = [r for r in p.rooms if r["kind"] == label]
        have = len(rooms) * len(DAYS) * len(TT_HOURS) - sum(
            1 for (rid, d, h) in p.blocked if h in TT_HOURS and any(r["id"] == rid for r in rooms))
        if need > have:
            out.append(f"{need} {kind} hours a week are needed but the {len(rooms)} {label}s "
                       f"only offer {have}.")
    return out


# ------------------------------------------------------------ constructing
def reserve_lunch(p, board):
    """Give every division a reserved lunch hour per day, rotating through
    12-1, 1-2 and 2-3 so each hour gets about a third of the students."""
    roots = sorted({p.root(g) for g in p.groups},
                   key=lambda g: (-sum(p.groups[x]["size"] for x in board.leaves_of(g)), g))
    load = defaultdict(int)
    for g in roots:
        size = sum(p.groups[x]["size"] for x in board.leaves_of(g))
        board.lunch[g] = {}
        for d in p.group_days(g):                    # only days the class is on campus
            h = min(LUNCH_HOURS, key=lambda x: (load[d, x], x))
            board.lunch[g][d] = h
            load[d, h] += size


def _slot_score(p, board, s, d, h):
    """Lower is better. Mixes stated preferences with tidy, compact days."""
    score = 10.0 * p.soft_cost(s, d, h)
    fam_hours = set()
    for leaf in board.leaves_of(s["group"]):
        fam_hours.update(board.leaf_hours(leaf, d))
    if not fam_hours:
        days_used = sum(1 for x in DAYS if x != d and any(
            board.leaf_hours(leaf, x) for leaf in board.leaves_of(s["group"])))
        md = p.pref_of(s["group"]).get("max_days")
        score += 80 if md and days_used >= md else 6
    else:
        mine = range(h, h + s["dur"])
        if (h - 1) in fam_hours or (h + s["dur"]) in fam_hours:
            score -= 4
        else:
            score += 2 * min(abs(x - y) for x in mine for y in fam_hours)
        score += 0.6 * len(fam_hours)
    tp = p.tpref.get(s["teacher"], {})
    if tp.get("max_days"):
        used = board.teacher_days.get(s["teacher"], set())
        if d not in used and len(used) >= tp["max_days"]:
            score += 50
    return score + 0.15 * (h - TT_HOURS[0]) + 0.01 * DAYS.index(d)


def construct(p, seed=0):
    """Place every session greedily, most constrained first, then repair
    leftovers by moving one or two blocking classes. -> Board."""
    import random
    from board import Board
    rng = random.Random(seed)
    b = Board(p)
    b.teacher_days = defaultdict(set)
    reserve_lunch(p, b)
    options = {s["id"]: [(d, h) for d in DAYS for h in TT_HOURS if p.hard_ok(s, d, h)]
               for s in p.sessions}
    t_load = defaultdict(int)
    for s in p.sessions:
        t_load[s["teacher"]] += s["dur"]
    order = sorted(p.sessions, key=lambda s: (s["kind"] != "lab", len(options[s["id"]]),
                                              -t_load[s["teacher"]],
                                              -s["size"], rng.random()))

    def put(s, strict):
        best = None
        for d, h in options[s["id"]]:
            if b.why_not(s, d, h, strict_lunch=strict):
                continue
            sc = _slot_score(p, b, s, d, h)
            if best is None or sc < best[0]:
                best = (sc, d, h)
        if best:
            b.place(s["id"], best[1], best[2], b.best_room(s, best[1], best[2]))
            b.teacher_days[s["teacher"]].add(best[1])
            return True
        return False

    left = [s for s in order if not put(s, True)]
    left = [s for s in left if not put(s, False)]       # any lunch hour, not just the reserved one
    for _ in range(3):                                   # repair: eject up to two blockers
        still = []
        for s in left:
            done = False
            cands = sorted(options[s["id"]], key=lambda dh: len(b.conflicts(s, *dh)))
            for d, h in cands:
                blockers = list(b.conflicts(s, d, h))
                if len(blockers) > 2:
                    break
                saved = [(x, *b.remove(x)) for x in blockers]
                if b.try_place(s["id"], d, h):
                    if all(put(b.sess[x], False) for x, *_ in saved):
                        done = True
                        break
                    for x, *_ in saved:                   # undo: someone could not be re-homed
                        if x in b.time:
                            b.remove(x)
                    b.remove(s["id"])
                for x, dd, hh, rr in saved:
                    b.place(x, dd, hh, rr)
            if not done:
                still.append(s)
        if len(still) == len(left):
            break
        left = still
    return b


def improve(p, b, seconds=60, workers=8, seed=0, progress=None):
    """Polish a constructed timetable with CP-SAT, one department-year at a time.

    A department's sessions are lifted off the board and re-timed by the
    exact solver against everything else (which stays fixed), starting from
    where they were. If the solver's answer cannot be roomed, the department
    is put back exactly as it was. -> list of per-department results.

    If OR-Tools could not be loaded, the timetable is left as constructed
    (still complete and valid) and an empty list is returned."""
    if cp_model is None:
        return []
    by_dept = defaultdict(list)
    for s in p.sessions:
        if s["id"] in b.time:
            g = p.groups[s["group"]]
            by_dept[g["dept"], g["year"]].append(s)        # one year of one department
    results = []
    share = max(2.0, seconds / max(1, len(by_dept)))
    for (dept, year), ss in sorted(by_dept.items(), key=lambda kv: -len(kv[1])):
        name = f"{p.depts.get(dept, 'No department')} year {year}"
        old = {s["id"]: (b.time[s["id"]], b.room[s["id"]]) for s in ss}
        for s in ss:
            b.remove(s["id"])
        new = _retime(p, b, ss, old, share, workers, seed)
        ok = new is not None
        if ok:
            order = sorted(ss, key=lambda s: (s["kind"] != "lab", -s["size"], s["id"]))
            for s in order:
                if not b.try_place(s["id"], *new[s["id"]]):
                    ok = False
                    break
            if not ok:
                for s in ss:
                    if s["id"] in b.time:
                        b.remove(s["id"])
        if not ok:
            for s in ss:
                (d, h), r = old[s["id"]]
                b.place(s["id"], d, h, r)
        moved = sum(1 for s in ss if ok and new[s["id"]] != old[s["id"]][0])
        results.append(dict(department=name, sessions=len(ss), moved=moved, kept=not ok))
        if progress:
            progress(results[-1])
    return results


def _retime(p, b, ss, old, seconds, workers, seed):
    """CP-SAT model for one department's sessions; the board holds everyone else."""
    m = cp_model.CpModel()
    x, by_s = {}, defaultdict(list)
    cover_t, cover_g = defaultdict(list), defaultdict(list)
    cost = []
    for s in ss:
        (od, oh), _ = old[s["id"]]
        for d in DAYS:
            for h in TT_HOURS:
                here = (d, h) == (od, oh)
                if not here and (b.why_not(s, d, h, strict_lunch=True) is not None):
                    continue
                if here and (b.conflicts(s, d, h) or not b.free_rooms(s, d, h)):
                    return None                       # cannot even restore: leave it be
                v = m.NewBoolVar(f"x{s['id']}_{d}_{h}")
                x[s["id"], d, h] = v
                by_s[s["id"]].append((d, h, v))
                m.AddHint(v, 1 if here else 0)
                for k in range(s["dur"]):
                    cover_t[s["teacher"], d, h + k].append(v)
                    cover_g[s["group"], d, h + k].append(v)
                c = p.soft_cost(s, d, h)
                if c:
                    cost.append(10 * c * v)
        m.AddExactlyOne(v for _, _, v in by_s[s["id"]])
    for vs in cover_t.values():
        if len(vs) > 1:
            m.AddAtMostOne(vs)

    def occ(g, d, h):
        vs = list(cover_g.get((g, d, h), []))
        par = p.groups[g]["parent"]
        return vs + (cover_g.get((par, d, h), []) if par else [])

    groups = {s["group"] for s in ss}
    leaves = {leaf for g in groups for leaf in b.leaves_of(g)}
    for g in leaves | groups:
        for d in DAYS:
            for h in TT_HOURS:
                vs = occ(g, d, h)
                if len(vs) > 1:
                    m.AddAtMostOne(vs)
    per_load = defaultdict(list)
    for s in ss:
        per_load[s["load"]].append(s)
    for load, group_ss in per_load.items():
        cap = b.day_cap(group_ss[0])
        for d in DAYS:
            vs = [v for s in group_ss for (dd, h, v) in by_s[s["id"]] if dd == d]
            if len(vs) > cap - b.load_day[load, d]:
                m.Add(sum(vs) <= cap - b.load_day[load, d])

    def room_limits(kind_ss, rooms):                  # big classes never outnumber big free rooms
        for d in DAYS:
            for h in TT_HOURS:
                free = [r for r in rooms if (r["id"], d, h) not in p.blocked
                        and (r["id"], d, h) not in b.r_busy]
                for c in [-1] + sorted({r["capacity"] for r in rooms})[:-1]:
                    vs = [x[s["id"], d, hh] for s in kind_ss if s["size"] > c
                          for hh in range(h - s["dur"] + 1, h + 1) if (s["id"], d, hh) in x]
                    cap = sum(r["capacity"] > c for r in free)
                    if len(vs) > cap:
                        m.Add(sum(vs) <= cap)

    room_limits([s for s in ss if s["kind"] == "lecture"], [r for r in p.rooms if r["kind"] == "classroom"])
    labs = [r for r in p.rooms if r["kind"] == "lab"]
    lab_ss = [s for s in ss if s["kind"] == "lab"]
    room_limits(lab_ss, labs)
    for lt in {(s["lab_type"] or "").upper() for s in lab_ss} - {""}:
        room_limits([s for s in lab_ss if (s["lab_type"] or "").upper() == lt],
                    [r for r in labs if (r["lab_type"] or "").upper() == lt])

    for leaf in leaves:                               # lunch, counting classes that stay fixed
        for d in DAYS:
            fixed = sum(b.leaf_busy(leaf, d, h) for h in LUNCH_HOURS)
            vs = [v for h in LUNCH_HOURS for v in occ(leaf, d, h)]
            if vs and fixed + len(vs) > 2:
                m.Add(sum(vs) <= 2 - fixed)
    for t in {s["teacher"] for s in ss}:
        for d in DAYS:
            fixed = sum((t, d, h) in b.t_busy for h in LUNCH_HOURS)
            vs = [v for h in LUNCH_HOURS for v in cover_t.get((t, d, h), [])]
            if vs and fixed + len(vs) > 2:
                m.Add(sum(vs) <= 2 - fixed)

    for leaf in leaves:                               # compact days and days-per-week
        flags = []
        for d in DAYS:
            fixed = b.leaf_hours(leaf, d)
            dv = [v for h in TT_HOURS for v in occ(leaf, d, h)]
            if not dv:
                flags.append(1 if fixed else 0)
                continue
            lo = m.NewIntVar(TT_HOURS[0], min(fixed) if fixed else LAST_END, f"f{leaf}_{d}")
            hi = m.NewIntVar(max(fixed) + 1 if fixed else TT_HOURS[0], LAST_END, f"l{leaf}_{d}")
            for h in TT_HOURS:
                vs = occ(leaf, d, h)
                if vs:
                    m.Add(hi >= (h + 1) * sum(vs))
                    m.Add(lo <= h + (LAST_END - h) * (1 - sum(vs)))
            sp = m.NewIntVar(0, LAST_END - TT_HOURS[0], f"sp{leaf}_{d}")
            m.Add(sp >= hi - lo)
            cost.append(3 * sp)
            if fixed:
                flags.append(1)
            else:
                u = m.NewBoolVar(f"u{leaf}_{d}")
                m.AddMaxEquality(u, dv)
                flags.append(u)
        md = p.pref_of(leaf).get("max_days")
        if md:
            if p.pref_of(leaf).get("hard"):
                m.Add(sum(flags) <= md)
            else:
                ov = m.NewIntVar(0, 6, f"ov{leaf}")
                m.Add(ov >= sum(flags) - md)
                cost.append(80 * ov)
    for t in {s["teacher"] for s in ss}:
        md = p.tpref.get(t, {}).get("max_days")
        if not md:
            continue
        flags = []
        for d in DAYS:
            if any((t, d, h) in b.t_busy for h in TT_HOURS):
                flags.append(1)
                continue
            vs = [v for h in TT_HOURS for v in cover_t.get((t, d, h), [])]
            if vs:
                u = m.NewBoolVar(f"tu{t}_{d}")
                m.AddMaxEquality(u, vs)
                flags.append(u)
        if p.tpref[t].get("hard"):
            m.Add(sum(flags) <= md)
        else:
            ov = m.NewIntVar(0, 6, f"tov{t}")
            m.Add(ov >= sum(flags) - md)
            cost.append(50 * ov)

    m.Minimize(sum(cost))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(seconds)
    solver.parameters.num_workers = workers
    solver.parameters.random_seed = seed
    if solver.Solve(m) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    return {sid: (d, h) for (sid, d, h), v in x.items() if solver.Value(v)}


def board_rows(p, b):
    return to_rows(p, b.time, b.room)


def unplaced_reasons(p, b):
    out = []
    for s in p.sessions:
        if s["id"] in b.time:
            continue
        reasons = defaultdict(int)
        for d in DAYS:
            for h in TT_HOURS:
                if h + s["dur"] <= LAST_END:
                    reasons[b.why_not(s, d, h) or "free"] += 1
        top = sorted(((n, r) for r, n in reasons.items() if r != "free"), reverse=True)
        out.append(dict(session=s, reason="No slot works. " + "; ".join(
            f"{n} slots: {r}" for n, r in top[:3]) + "."))
    return out


def to_rows(p, times, room_of):
    """Placement rows: a 2-hour lab becomes two rows sharing a session_key."""
    rows = []
    for s in p.sessions:
        if s["id"] not in times or s["id"] not in room_of:
            continue
        d, h = times[s["id"]]
        g, t = p.groups[s["group"]], p.teachers[s["teacher"]]
        for k in range(s["dur"]):
            rows.append(dict(room_id=room_of[s["id"]], day=d, hour=h + k, group_id=s["group"],
                             teacher_id=s["teacher"], subject=s["subject"], kind=s["kind"],
                             label=f"{g['code']} · {s['subject']} · {t['name']}"[:200],
                             session_key=f"s{s['id']}"))
    return rows


def generate(engine, seed=0, improve_seconds=0, progress=None):
    """One call. -> (problem, rows, unplaced, info)."""
    import time
    t0 = time.time()
    p = load_problem(engine)
    b = construct(p, seed)
    info = {"sessions": len(p.sessions), "placed": len(b.time)}
    if improve_seconds:
        info["improve"] = improve(p, b, improve_seconds, seed=seed, progress=progress)
    info["seconds"] = round(time.time() - t0, 1)
    return p, board_rows(p, b), unplaced_reasons(p, b), info
