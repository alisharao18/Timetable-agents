"""Hand edits to the published timetable: move a class, swap two, and lock
classes so the generator keeps them where they are.

Every edit goes through the same Board rule checks the generator uses, so a
hand edit can never create a clash. Edited classes are saved with source
'manual'. Locks are stored by class code, subject, teacher and session
number, so they survive re-uploading the input workbook.
"""
from datetime import datetime

from sqlalchemy import delete, insert
from sqlalchemy.exc import IntegrityError

import db
import solver
from board import Board
from constants import DAYS, TT_HOURS, hour_label


def current(engine):
    """-> (problem, board) holding the published timetable. Cells used by
    anything that is not a class session (imported labels, blocked cells)
    count as taken."""
    p = solver.load_problem(engine)
    rows = db.q(engine, "select * from placements")
    for r in rows:
        if r.get("session_key") not in p.by_key:
            p.blocked.add((r["room_id"], r["day"], r["hour"]))
    b = Board(p).load_rows([r for r in rows if r.get("session_key") in p.by_key])
    return p, b


def _room_name(p, rid):
    return next((r["name"] for r in p.rooms if r["id"] == rid), "?")


def describe(p, b, s):
    d, h = b.time.get(s["id"], (None, None))
    when = f"{d} {hour_label(h)}" if d else "not placed"
    room = _room_name(p, b.room[s["id"]]) if s["id"] in b.room else "-"
    tag = " [elective]" if s.get("block") else (" [locked]" if s.get("lock") else "")
    who = p.groups[s["group"]]["code"]
    return f"{when} · {who} · {s['subject']} · {p.teachers[s['teacher']]['name']} · {room}{tag}"


def sessions(p, b, group_code=None, teacher=None):
    """The class sessions of one class (with its batches and parent division)
    or one teacher, in week order."""
    if group_code:
        gid = next((g for g, v in p.groups.items() if v["code"] == group_code), None)
        if gid is None:
            return []
        family = {gid, p.groups[gid]["parent"]} | {g for g, v in p.groups.items() if v["parent"] == gid}
        pick = [s for s in p.sessions if family & set(p.groups_of(s))]
    else:
        tid = next((t for t, v in p.teachers.items() if v["name"] == teacher), None)
        pick = [s for s in p.sessions if s["teacher"] == tid]
    order = lambda s: (DAYS.index(b.time[s["id"]][0]), b.time[s["id"]][1]) if s["id"] in b.time else (9, 99)
    return [dict(key=s["key"], text=describe(p, b, s), movable=not s.get("block"),
                 locked=bool(s.get("lock")))
            for s in sorted(pick, key=order)]


def options(p, b, key):
    """Every legal new start time for a session, with the free rooms that
    fit (own department's first). -> list of {day, hour, rooms}."""
    s = b.sess[p.by_key[key]]
    if s.get("block"):
        return []
    saved = (b.time[s["id"]], b.room[s["id"]]) if s["id"] in b.time else None
    if saved:
        b.remove(s["id"])
    out = []
    try:
        for d in DAYS:
            for h in TT_HOURS:
                if h + s["dur"] > TT_HOURS[-1] + 1 or b.why_not(s, d, h):
                    continue
                own = b.own[s["group"]]
                rooms = sorted(b.free_rooms(s, d, h), key=lambda r: (r["id"] not in own, r["capacity"]))
                out.append(dict(day=d, hour=h, rooms=[r["name"] for r in rooms],
                                current=saved is not None and saved[0] == (d, h)))
    finally:
        if saved:
            b.place(s["id"], *saved[0], saved[1])
    return out


def _save(engine, p, b, sids, user, action, detail):
    rows = solver.to_rows(p, {x: b.time[x] for x in sids}, {x: b.room[x] for x in sids})
    keys = [p.key_of(b.sess[x]) for x in sids]
    try:
        with engine.begin() as c:
            c.execute(delete(db.placements).where(db.placements.c.session_key.in_(keys)))
            c.execute(insert(db.placements), [{**r, "source": "manual"} for r in rows])
    except IntegrityError:
        return False, "That room was taken by someone else a moment ago. Nothing was changed; try again."
    db.log(engine, user, action, detail)
    return True, detail


def move(engine, key, day, hour, room=None, user="admin"):
    """Move one class session. -> (ok, message)."""
    p, b = current(engine)
    sid = p.by_key.get(key)
    if sid is None:
        return False, "That class is no longer in the teaching load."
    s = b.sess[sid]
    if s.get("block"):
        return False, ("This is part of an elective block, whose options must stay together. "
                       "Change the block in the Electives tab and generate again.")
    if s.get("lock"):
        return False, "This class is locked. Unlock it first."
    rid = None
    if room:
        rid = next((r["id"] for r in p.rooms if r["name"] == room), None)
        if rid is None:
            return False, f"There is no room called {room}."
    saved = (b.time[sid], b.room[sid]) if sid in b.time else None
    if saved:
        b.remove(sid)
    why = b.why_not(s, day, hour, rid)
    if why:
        if saved:
            b.place(sid, *saved[0], saved[1])
        return False, f"Not moved: {why}."
    b.place(sid, day, hour, rid if rid is not None else b.best_room(s, day, hour))
    return _save(engine, p, b, [sid], user, "move",
                 f"Moved {describe(p, b, s)}")


def swap(engine, key_a, key_b, user="admin"):
    """Swap the times of two sessions (each keeps or finds a fitting room)."""
    p, b = current(engine)
    a, c = (p.by_key.get(k) for k in (key_a, key_b))
    if a is None or c is None or a not in b.time or c not in b.time:
        return False, "Both classes must be in the published timetable."
    sa, sc = b.sess[a], b.sess[c]
    if sa.get("block") or sc.get("block"):
        return False, "Elective block classes cannot be swapped by hand."
    if sa.get("lock") or sc.get("lock"):
        return False, "One of these classes is locked. Unlock it first."
    old = {x: (b.time[x], b.room[x]) for x in (a, c)}
    b.remove(a)
    b.remove(c)
    (da, ha), ra = old[a]
    (dc, hc), rc = old[c]
    ok = False
    if not b.why_not(sa, dc, hc):
        b.place(a, dc, hc, rc if rc in {r["id"] for r in b.free_rooms(sa, dc, hc)} else b.best_room(sa, dc, hc))
        if not b.why_not(sc, da, ha):
            b.place(c, da, ha, ra if ra in {r["id"] for r in b.free_rooms(sc, da, ha)} else b.best_room(sc, da, ha))
            ok = True
        else:
            why = b.why_not(sc, da, ha)
            b.remove(a)
    else:
        why = b.why_not(sa, dc, hc)
    if not ok:
        for x, ((d, h), r) in old.items():
            b.place(x, d, h, r)
        return False, f"Not swapped: {why}."
    return _save(engine, p, b, [a, c], user, "swap",
                 f"Swapped {describe(p, b, sa)} and {describe(p, b, sc)}")


def lock(engine, keys, user="admin"):
    """Pin sessions where they are now. -> (ok, message)."""
    p, b = current(engine)
    rows, skipped = [], 0
    for key in keys:
        sid = p.by_key.get(key)
        if sid is None or sid not in b.time or b.sess[sid].get("block") or b.sess[sid].get("lock"):
            skipped += 1
            continue
        s = b.sess[sid]
        d, h = b.time[sid]
        rows.append(dict(group_code=p.groups[s["group"]]["code"], subject=s["subject"],
                         teacher=p.teachers[s["teacher"]]["name"], n=s["n"], day=d, hour=h,
                         room=_room_name(p, b.room[sid]), locked_by=user, at=datetime.now()))
    if rows:
        with engine.begin() as c:
            c.execute(insert(db.locks), rows)
        db.log(engine, user, "lock", f"{len(rows)} class session(s)")
    note = f" ({skipped} skipped: already locked, elective or not placed)" if skipped else ""
    return bool(rows), f"Locked {len(rows)} class session(s){note}."


def unlock(engine, keys, user="admin"):
    p = solver.load_problem(engine)
    n = 0
    with engine.begin() as c:
        for key in keys:
            sid = p.by_key.get(key)
            if sid is None:
                continue
            s = next(x for x in p.sessions if x["id"] == sid)
            n += c.execute(delete(db.locks).where(
                (db.locks.c.group_code == p.groups[s["group"]]["code"]) & (db.locks.c.subject == s["subject"])
                & (db.locks.c.teacher == p.teachers[s["teacher"]]["name"]) & (db.locks.c.n == s["n"]))).rowcount
    db.log(engine, user, "unlock", f"{n} class session(s)")
    return n > 0, f"Unlocked {n} class session(s)."


def all_locks(engine):
    return db.df(engine, """select group_code as "Class", subject as "Subject", teacher as "Teacher",
                            day as "Day", hour as "Hour", room as "Room", locked_by as "Locked by"
                            from locks order by group_code, day, hour""")
