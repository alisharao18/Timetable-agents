"""Keeps the database in step with the master sheet.

Google Sheet: a small script on the sheet (sheet_script.gs) drops every edit
into the `sheet_edits` inbox table. `process_inbox` applies each one through
the same rules as the first import. An edit that clashes with something
already booked is REJECTED and kept in the list for the coordinator; it never
overwrites a booking.

Excel file: upload it again and `diff_upload` shows what changed first.
"""
import re
from datetime import datetime

import pandas as pd
from sqlalchemy import delete, insert

import db
import importer
from constants import BOOK_HOURS, DAYS


def _resolve_room(engine, raw, sheet):
    names = {r["name"].lower(): r for r in db.q(engine, "select id, name from rooms")}
    raw = re.sub(r"\s+", " ", str(raw or "")).strip()
    if raw.lower() in names:
        return names[raw.lower()]
    guess = importer._lab_name(raw, importer._floor(sheet))     # a lab block's long title
    return names.get(guess.lower())


def _day(raw):
    d = str(raw or "").strip()[:3].title()
    return d if d in DAYS else None


def apply_edit(engine, e):
    """-> (status, note) for one inbox row."""
    room = _resolve_room(engine, e["room"], e["sheet"])
    day = _day(e["day"])
    hour = e["hour"]
    if room is None:
        return "rejected", f"Room '{e['room']}' is not in the room list."
    if day is None or hour not in BOOK_HOURS:
        return "rejected", "The edited cell is not inside a timetable grid."
    rid = room["id"]
    year = (e["at"] or datetime.now()).year if not isinstance(e["at"], str) else int(str(e["at"])[:4])
    kind, dates = importer.classify_cell(e["new_text"], year, day)
    text = re.sub(r"\s+", " ", str(e["new_text"] or "")).strip()
    here = db.q(engine, "select id, source, label from placements where room_id=:r and day=:d and hour=:h",
                r=rid, d=day, h=hour)
    who = e["editor"] or "sheet"

    if kind == "empty":                                   # the cell was cleared
        if here and here[0]["source"] in ("sheet", "unresolved"):
            with engine.begin() as c:
                c.execute(delete(db.placements).where(db.placements.c.id == here[0]["id"]))
                c.execute(delete(db.unresolved).where(db.unresolved.c.room_id == rid,
                                                      db.unresolved.c.day == day,
                                                      db.unresolved.c.hour == hour))
            return "applied", "Weekly entry removed."
        old_kind, old_dates = importer.classify_cell(e["old_text"], year, day)
        if old_kind == "dated":
            with engine.begin() as c:
                c.execute(delete(db.bookings).where(
                    db.bookings.c.room_id == rid, db.bookings.c.hour == hour,
                    db.bookings.c.date.in_(old_dates), db.bookings.c.booked_by.in_([who, "sheet import"])))
            return "applied", "Dated booking removed."
        if here:
            return "rejected", "That cell holds a class from the published timetable; change it in the app."
        return "applied", "Nothing to remove."

    if kind in ("code", "unresolved"):
        if here and here[0]["source"] not in ("sheet", "unresolved"):
            return "rejected", (f"{room['name']} {day} {hour}:00 already holds '{here[0]['label']}' "
                                "from the published timetable.")
        with engine.begin() as c:
            if here:
                c.execute(delete(db.placements).where(db.placements.c.id == here[0]["id"]))
            c.execute(delete(db.unresolved).where(db.unresolved.c.room_id == rid,
                                                  db.unresolved.c.day == day, db.unresolved.c.hour == hour))
            label = text[:200] if kind == "code" else "CHECK: " + text[:190]
            c.execute(insert(db.placements).values(room_id=rid, day=day, hour=hour, label=label,
                                                   source="sheet" if kind == "code" else "unresolved"))
            if kind == "unresolved":
                c.execute(insert(db.unresolved).values(room_id=rid, day=day, hour=hour, text=text))
        return "applied", ("Weekly entry saved." if kind == "code" else
                           "Saved, but the text has no readable date: the slot is blocked until someone checks it.")

    # dated booking(s)
    if here:
        return "rejected", (f"{room['name']} has '{here[0]['label']}' every {day} at {hour}:00, "
                            "so it cannot be booked then.")
    clash = db.q(engine, "select date, booked_by, purpose from bookings where room_id=:r and hour=:h",
                 r=rid, h=hour)
    taken = {str(c["date"])[:10]: c for c in clash}
    hit = [d for d in dates if d.isoformat() in taken]
    if hit:
        c = taken[hit[0].isoformat()]
        return "rejected", (f"{room['name']} is already booked on {hit[0]:%d %b %Y} at {hour}:00 "
                            f"by {c['booked_by']} ({c['purpose']}).")
    with engine.begin() as c:
        c.execute(insert(db.bookings), [
            dict(room_id=rid, date=d, hour=hour, purpose=importer._purpose(text), booked_by=who,
                 department=None, created_at=datetime.now(), booking_key=f"sheet-{rid}-{d}-{hour}")
            for d in dates])
    return "applied", f"Booked for {', '.join(f'{d:%d %b}' for d in dates)}."


def process_inbox(engine):
    """Apply every pending sheet edit, oldest first. -> counts."""
    counts = {"applied": 0, "rejected": 0}
    for e in db.q(engine, "select * from sheet_edits where status='pending' or status is null order by id"):
        try:
            status, note = apply_edit(engine, e)
        except Exception as err:                          # one bad row must not block the rest
            status, note = "rejected", f"Could not be processed: {err}"
        db.run(engine, "update sheet_edits set status=:s, note=:n where id=:i",
               s=status, n=note[:1000], i=e["id"])
        counts[status] += 1
    if counts["applied"] or counts["rejected"]:
        db.log(engine, "sheet sync", "inbox", counts)
    return counts


def add_edit(engine, sheet, room, day, hour, old_text, new_text, editor):
    """What the sheet script does over the database's REST API; also handy for tests."""
    with engine.begin() as c:
        c.execute(insert(db.sheet_edits).values(at=datetime.now(), sheet=sheet, room=room, day=day,
                                                hour=hour, old_text=old_text, new_text=new_text,
                                                editor=editor, status="pending"))


def diff_upload(engine, result):
    """What a re-uploaded workbook would change in the weekly grid."""
    ids = db.room_ids(engine)
    now = {(w["room"], w["day"], w["hour"]): w["label"] for w in result.weekly}
    now.update({(u["room"], u["day"], u["hour"]): "CHECK: " + u["text"][:190] for u in result.unresolved})
    rooms = [r["name"] for r in result.rooms if r["name"] in ids]
    was = {}
    for p in db.q(engine, """select r.name, p.day, p.hour, p.label from placements p
                             join rooms r on r.id=p.room_id where p.source in ('sheet','unresolved')"""):
        if p["name"] in rooms:
            was[p["name"], p["day"], p["hour"]] = p["label"]
    rows = []
    for key in sorted(set(was) | set(now), key=lambda k: (k[0], DAYS.index(k[1]), k[2])):
        a, b = was.get(key, ""), now.get(key, "")
        if a != b:
            rows.append(dict(room=key[0], day=key[1], hour=key[2], was=a, now=b,
                             change="added" if not a else "removed" if not b else "changed"))
    new_rooms = [r["name"] for r in result.rooms if r["name"] not in ids]
    for n in new_rooms:
        rows.append(dict(room=n, day="", hour=None, was="", now="(new room)", change="new room"))
    return pd.DataFrame(rows, columns=["room", "day", "hour", "was", "now", "change"])
