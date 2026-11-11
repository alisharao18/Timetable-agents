"""The central database: schema, connection and the only write paths.

SQLite is used locally; set DATABASE_URL to a Postgres URL when hosted.
Unique indexes on (room, day, hour) and (room, date, hour) are what make a
double booking impossible, whoever is clicking and however fast.
"""
import os
from datetime import datetime

import pandas as pd
from sqlalchemy import (Boolean, Column, Date, DateTime, ForeignKey, Integer,
                        MetaData, String, Table, Text, UniqueConstraint,
                        create_engine, delete, insert, text)

md = MetaData()

rooms = Table(
    "rooms", md,
    Column("id", Integer, primary_key=True),
    Column("name", String(40), unique=True, nullable=False),
    Column("kind", String(12), nullable=False),          # classroom | lab
    Column("room_type", String(20)),                     # STEP | FLAT | LAB
    Column("capacity", Integer, nullable=False),
    Column("floor", Integer),
    Column("owner_label", String(200)),                  # free text from the sheet
    Column("lab_type", String(60)))                      # e.g. COMPUTER, PHYSICS

departments = Table(
    "departments", md,
    Column("id", Integer, primary_key=True),
    Column("name", String(80), unique=True, nullable=False))

department_rooms = Table(
    "department_rooms", md,
    Column("department_id", ForeignKey("departments.id"), nullable=False),
    Column("room_id", ForeignKey("rooms.id"), nullable=False),
    UniqueConstraint("department_id", "room_id"))

class_groups = Table(
    "class_groups", md,
    Column("id", Integer, primary_key=True),
    Column("code", String(60), unique=True, nullable=False),
    Column("department_id", ForeignKey("departments.id")),
    Column("programme", String(60)),
    Column("year", Integer),
    Column("size", Integer, nullable=False),
    Column("parent_id", ForeignKey("class_groups.id")))  # a batch's division

teachers = Table(
    "teachers", md,
    Column("id", Integer, primary_key=True),
    Column("name", String(100), unique=True, nullable=False),
    Column("department_id", ForeignKey("departments.id")))

teaching_load = Table(
    "teaching_load", md,
    Column("id", Integer, primary_key=True),
    Column("group_id", ForeignKey("class_groups.id"), nullable=False),
    Column("subject", String(100), nullable=False),
    Column("teacher_id", ForeignKey("teachers.id"), nullable=False),
    Column("hours", Integer, nullable=False),            # hours per week
    Column("kind", String(10), nullable=False),          # lecture | lab
    Column("lab_type", String(60)))

group_prefs = Table(
    "group_prefs", md,
    Column("group_id", ForeignKey("class_groups.id"), primary_key=True),
    Column("days_on_campus", String(40)),                # "Mon,Tue,Wed" ; blank = all
    Column("max_days", Integer),
    Column("saturday_ok", Boolean, default=True),
    Column("earliest", Integer),
    Column("latest", Integer),                           # last hour a class may END
    Column("hard", Boolean, default=False))              # soft prefs become rules

teacher_prefs = Table(
    "teacher_prefs", md,
    Column("teacher_id", ForeignKey("teachers.id"), primary_key=True),
    Column("unavailable_days", String(40)),              # "Sat" ; never broken
    Column("unavailable_hours", String(200)),            # "Mon-8,Mon-9" ; never broken
    Column("max_days", Integer),
    Column("preferred", String(12)),                     # morning | afternoon | blank
    Column("hard", Boolean, default=False))

placements = Table(                                      # the weekly timetable
    "placements", md,
    Column("id", Integer, primary_key=True),
    Column("room_id", ForeignKey("rooms.id"), nullable=False),
    Column("day", String(3), nullable=False),
    Column("hour", Integer, nullable=False),
    Column("group_id", ForeignKey("class_groups.id")),
    Column("teacher_id", ForeignKey("teachers.id")),
    Column("subject", String(100)),
    Column("label", String(200)),                        # what the grid cell shows
    Column("kind", String(10)),                          # lecture | lab
    Column("session_key", String(40)),                   # groups a 2-hour lab's rows
    Column("source", String(12), nullable=False),        # sheet|generated|unresolved|manual
    UniqueConstraint("room_id", "day", "hour", name="uq_placement_cell"))

bookings = Table(                                        # dated one-off bookings
    "bookings", md,
    Column("id", Integer, primary_key=True),
    Column("room_id", ForeignKey("rooms.id"), nullable=False),
    Column("date", Date, nullable=False),
    Column("hour", Integer, nullable=False),
    Column("department", String(80)),
    Column("purpose", String(300)),
    Column("booked_by", String(120)),
    Column("booking_key", String(40)),                   # groups a multi-hour booking
    Column("created_at", DateTime),
    UniqueConstraint("room_id", "date", "hour", name="uq_booking_cell"))

unresolved = Table(                                      # sheet cells a person must read
    "unresolved", md,
    Column("id", Integer, primary_key=True),
    Column("room_id", ForeignKey("rooms.id"), nullable=False),
    Column("day", String(3)), Column("hour", Integer), Column("text", Text))

users = Table(
    "users", md,
    Column("id", Integer, primary_key=True),
    Column("email", String(120), unique=True, nullable=False),
    Column("pw_hash", String(300), nullable=False),
    Column("role", String(12), nullable=False),
    Column("department", String(80)))

audit_log = Table(
    "audit_log", md,
    Column("id", Integer, primary_key=True),
    Column("at", DateTime), Column("user", String(120)),
    Column("action", String(40)), Column("detail", Text))

settings = Table(
    "settings", md,
    Column("key", String(60), primary_key=True), Column("value", Text))

sheet_edits = Table(                                     # inbox filled by the sheet script
    "sheet_edits", md,
    Column("id", Integer, primary_key=True),
    Column("at", DateTime), Column("sheet", String(60)),
    Column("room", String(40)), Column("day", String(3)), Column("hour", Integer),
    Column("old_text", Text), Column("new_text", Text), Column("editor", String(120)),
    Column("status", String(12), default="pending"),     # pending|applied|rejected
    Column("note", Text))


def get_engine(url=None):
    url = url or os.environ.get("DATABASE_URL") or "sqlite:///college.db"
    if url.startswith("postgres://"):                    # Supabase/Heroku style
        url = "postgresql://" + url[len("postgres://"):]
    kw = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False, "timeout": 30}
    return create_engine(url, **kw)


def init_db(engine):
    """Create any missing tables. Safe to call on every start."""
    md.create_all(engine)


def q(engine, sql, **params):
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(text(sql), params)]


def df(engine, sql, **params):
    return pd.DataFrame(q(engine, sql, **params))


def run(engine, sql, **params):
    with engine.begin() as c:
        c.execute(text(sql), params)


def log(engine, user, action, detail=""):
    with engine.begin() as c:
        c.execute(insert(audit_log).values(at=datetime.now(), user=user,
                                           action=action, detail=str(detail)[:2000]))


def get_setting(engine, key, default=None):
    r = q(engine, "select value from settings where key=:k", k=key)
    return r[0]["value"] if r else default


def set_setting(engine, key, value):
    with engine.begin() as c:
        c.execute(delete(settings).where(settings.c.key == key))
        c.execute(insert(settings).values(key=key, value=str(value)))


def replace_placements(engine, rows, source, clear_sources=None):
    """Swap in a new weekly timetable in ONE transaction: either all of it
    lands or none of it does. `clear_sources` are wiped first (defaults to
    just `source`)."""
    clear = list(clear_sources or [source])
    with engine.begin() as c:
        c.execute(delete(placements).where(placements.c.source.in_(clear)))
        if rows:
            c.execute(insert(placements), [{**r, "source": source} for r in rows])


def room_ids(engine):
    return {r["name"]: r["id"] for r in q(engine, "select id, name from rooms")}
