"""Find a free room for a date and time, and book it safely.

A room is free for an hour when nothing sits in that weekly timetable cell
AND nobody holds a dated booking for it. `book` relies on the database's
unique index, so when two people click at the same instant exactly one wins.
"""
import uuid
from datetime import date as Date, datetime, timedelta

from sqlalchemy import delete, insert, select
from sqlalchemy.exc import IntegrityError

import db
from constants import BOOK_HOURS, DAYS


def _busy(engine, on, hours):
    """Room ids that are taken for any of `hours` on date `on`."""
    day, hours = DAYS[on.weekday()], [int(h) for h in hours]
    P, B = db.placements.c, db.bookings.c
    with engine.connect() as c:
        a = c.execute(select(P.room_id).where(P.day == day, P.hour.in_(hours))).all()
        b = c.execute(select(B.room_id).where(B.date == on, B.hour.in_(hours))).all()
    return {r[0] for r in a} | {r[0] for r in b}


def _owned(engine, department):
    if not department:
        return set()
    return {r["room_id"] for r in db.q(engine, """
        select x.room_id from department_rooms x join departments d on d.id = x.department_id
        where lower(d.name) = lower(:n)""", n=department)}


def check_request(on, start_hour, duration):
    """Plain-language reason a request cannot be searched, or None."""
    if duration < 1:
        return "Duration must be at least one hour."
    if start_hour not in BOOK_HOURS or start_hour + duration - 1 not in BOOK_HOURS:
        return "Rooms can be booked between 7 AM and 7 PM."
    return None


def find_rooms(engine, on, start_hour, duration=1, kind="classroom", seats=1,
               room_type=None, lab_type=None, department=None):
    """Free rooms that fit, best first: the requester's own department's
    rooms, then the snuggest fit, then the lowest floor."""
    if check_request(on, start_hour, duration):
        return []
    busy = _busy(engine, on, range(start_hour, start_hour + duration))
    own = _owned(engine, department)
    out = []
    for r in db.q(engine, "select * from rooms where kind=:k and capacity>=:s", k=kind, s=seats):
        if r["id"] in busy:
            continue
        if room_type and (r["room_type"] or "").upper() != room_type.upper():
            continue
        if lab_type and (r["lab_type"] or "").upper() != lab_type.upper():
            continue
        out.append({"room": r["name"], "capacity": r["capacity"], "room_type": r["room_type"],
                    "lab_type": r["lab_type"], "floor": r["floor"],
                    "owner": r["owner_label"], "own_department": r["id"] in own,
                    "spare_seats": r["capacity"] - seats})
    out.sort(key=lambda r: (not r["own_department"], r["spare_seats"], r["floor"] or 99, r["room"]))
    return out


def nearest_free(engine, on, start_hour, duration=1, limit=5, **need):
    """When nothing is free at the asked time: the closest other times, the
    same day first, then the following days."""
    found = []
    for offset in range(0, 8):
        d = on + timedelta(days=offset)
        hours = sorted(BOOK_HOURS, key=lambda h: abs(h - start_hour))
        for h in hours:
            if (offset == 0 and h == start_hour) or check_request(d, h, duration):
                continue
            rooms = find_rooms(engine, d, h, duration, **need)
            if rooms:
                found.append({"date": d, "start_hour": h, "room": rooms[0]["room"],
                              "rooms_free": len(rooms)})
                if len(found) >= limit:
                    return found
    return found


def book(engine, room, on, start_hour, duration, department, purpose, user):
    """-> (ok, message). All hours are booked together or not at all."""
    bad = check_request(on, start_hour, duration)
    if bad:
        return False, bad
    if not (purpose or "").strip():
        return False, "Say what the room is needed for."
    r = db.q(engine, "select id from rooms where name=:n", n=room)
    if not r:
        return False, f"There is no room called {room}."
    rid = r[0]["id"]
    hours = list(range(start_hour, start_hour + duration))
    if rid in _busy(engine, on, hours):
        return False, f"{room} is no longer free at that time. Search again for alternatives."
    key = uuid.uuid4().hex[:16]
    try:
        with engine.begin() as c:
            c.execute(insert(db.bookings), [
                dict(room_id=rid, date=on, hour=h, department=department,
                     purpose=purpose.strip()[:300], booked_by=user, booking_key=key,
                     created_at=datetime.now()) for h in hours])
    except IntegrityError:
        return False, f"{room} was just booked by someone else. Search again for alternatives."
    db.log(engine, user, "book", f"{room} {on} {start_hour}:00 x{duration}h: {purpose}")
    return True, f"Booked {room} on {on:%a %d %b %Y}, {start_hour}:00 to {start_hour + duration}:00."


def list_bookings(engine, user=None, from_date=None):
    sql = """select b.booking_key, r.name as room, b.date, min(b.hour) as start_hour,
                    count(*) as hours, b.department, b.purpose, b.booked_by
             from bookings b join rooms r on r.id = b.room_id where 1=1"""
    p = {}
    if user:
        sql += " and b.booked_by = :u"
        p["u"] = user
    if from_date:
        sql += " and b.date >= :f"
        p["f"] = from_date
    sql += " group by b.booking_key, r.name, b.date, b.department, b.purpose, b.booked_by order by b.date, r.name"
    return db.q(engine, sql, **p)


def cancel(engine, booking_key, user, is_admin=False):
    rows = db.q(engine, "select booked_by from bookings where booking_key=:k", k=booking_key)
    if not rows:
        return False, "That booking no longer exists."
    if not is_admin and rows[0]["booked_by"] != user:
        return False, "Only the person who made a booking, or an admin, can cancel it."
    with engine.begin() as c:
        c.execute(delete(db.bookings).where(db.bookings.c.booking_key == booking_key))
    db.log(engine, user, "cancel", booking_key)
    return True, "Booking cancelled."
