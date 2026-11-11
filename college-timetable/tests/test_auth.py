import auth
import db


def test_login_roundtrip_and_wrong_password(engine):
    assert auth.create_user(engine, "Admin@College.edu", "correct horse", "admin")[0]
    u = auth.check_login(engine, "admin@college.edu ", "correct horse")
    assert u == {"email": "admin@college.edu", "role": "admin", "department": None}
    assert auth.check_login(engine, "admin@college.edu", "wrong") is None
    assert auth.check_login(engine, "nobody@college.edu", "correct horse") is None
    stored = db.q(engine, "select pw_hash from users")[0]["pw_hash"]
    assert "correct horse" not in stored and stored.startswith("pbkdf2_sha256$")


def test_bad_accounts_are_refused(engine):
    assert not auth.create_user(engine, "a@b.c", "short", "viewer")[0]
    assert not auth.create_user(engine, "a@b.c", "long enough", "boss")[0]
    assert not auth.create_user(engine, "not-an-email", "long enough", "viewer")[0]
    assert auth.create_user(engine, "a@b.c", "long enough", "viewer")[0]
    assert not auth.create_user(engine, "A@B.C", "long enough", "viewer")[0]


def test_roles(engine):
    assert auth.can("admin", "generate") and auth.can("admin", "cancel_any")
    assert auth.can("coordinator", "book") and not auth.can("coordinator", "generate")
    assert auth.can("viewer", "view") and not auth.can("viewer", "book")
    assert not auth.can("stranger", "view")


def test_first_admin_and_last_admin(engine):
    assert "No admin account" in auth.ensure_admin(engine)
    assert "Created admin" in auth.ensure_admin(engine, "x@y.z", "long enough")
    assert auth.ensure_admin(engine) is None
    assert not auth.remove_user(engine, "x@y.z")[0]
    assert auth.set_password(engine, "x@y.z", "another pass")[0]
    assert auth.check_login(engine, "x@y.z", "another pass")
