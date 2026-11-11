from datetime import date
import pytest
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
import db


def _room(engine):
    with engine.begin() as c:
        c.execute(insert(db.rooms).values(name="R1", kind="classroom", capacity=60))
    return db.room_ids(engine)["R1"]


def test_init_twice_is_harmless(engine):
    db.init_db(engine)
    assert db.q(engine, "select count(*) n from rooms")[0]["n"] == 0


def test_same_weekly_cell_twice_is_refused(engine):
    rid = _room(engine)
    row = dict(room_id=rid, day="Mon", hour=9, label="x", source="manual")
    with engine.begin() as c:
        c.execute(insert(db.placements).values(**row))
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(insert(db.placements).values(**row))


def test_same_dated_cell_twice_is_refused(engine):
    rid = _room(engine)
    row = dict(room_id=rid, date=date(2026, 11, 20), hour=9)
    with engine.begin() as c:
        c.execute(insert(db.bookings).values(**row))
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(insert(db.bookings).values(**row))


def test_replace_placements_is_all_or_nothing(engine):
    rid = _room(engine)
    good = [dict(room_id=rid, day="Mon", hour=9, label="a")]
    db.replace_placements(engine, good, "generated")
    bad = [dict(room_id=rid, day="Tue", hour=9, label="b"),
           dict(room_id=rid, day="Tue", hour=9, label="c")]
    with pytest.raises(IntegrityError):
        db.replace_placements(engine, bad, "generated")
    rows = db.q(engine, "select label from placements")
    assert [r["label"] for r in rows] == ["a"]


def test_settings_roundtrip(engine):
    assert db.get_setting(engine, "canteen_limit", "x") == "x"
    db.set_setting(engine, "canteen_limit", 900)
    db.set_setting(engine, "canteen_limit", 950)
    assert db.get_setting(engine, "canteen_limit") == "950"
