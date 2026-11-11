"""Sign-in and roles. Passwords are stored only as salted PBKDF2 hashes."""
import hashlib
import hmac
import os
import secrets

from sqlalchemy import delete, insert

import db
from constants import ROLES

ITERATIONS = 240_000
ALLOWED = {
    "viewer": {"view"},
    "coordinator": {"view", "book", "cancel_own"},
    "admin": {"view", "book", "cancel_own", "cancel_any", "import", "generate", "users"},
}


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest.hex()}"


def verify_password(password, stored):
    try:
        _, iters, salt, digest = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(got.hex(), digest)
    except (ValueError, AttributeError):
        return False


def create_user(engine, email, password, role, department=None):
    """-> (ok, message)."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return False, "Enter a valid email address."
    if role not in ROLES:
        return False, f"Role must be one of: {', '.join(ROLES)}."
    if len(password or "") < 8:
        return False, "Password must be at least 8 characters."
    if db.q(engine, "select 1 from users where email=:e", e=email):
        return False, f"{email} already has an account."
    with engine.begin() as c:
        c.execute(insert(db.users).values(email=email, pw_hash=hash_password(password),
                                          role=role, department=department or None))
    return True, f"Created {role} account for {email}."


def set_password(engine, email, password):
    if len(password or "") < 8:
        return False, "Password must be at least 8 characters."
    db.run(engine, "update users set pw_hash=:h where email=:e",
           h=hash_password(password), e=email.strip().lower())
    return True, "Password changed."


def remove_user(engine, email):
    admins = db.q(engine, "select email from users where role='admin'")
    if [a["email"] for a in admins] == [email]:
        return False, "This is the only admin account, so it cannot be removed."
    with engine.begin() as c:
        c.execute(delete(db.users).where(db.users.c.email == email))
    return True, f"Removed {email}."


def check_login(engine, email, password):
    """-> user dict (email, role, department) or None."""
    rows = db.q(engine, "select email, pw_hash, role, department from users where email=:e",
                e=(email or "").strip().lower())
    if rows and verify_password(password or "", rows[0]["pw_hash"]):
        return {k: rows[0][k] for k in ("email", "role", "department")}
    return None


def ensure_admin(engine, email=None, password=None):
    """Create the first admin from ADMIN_EMAIL / ADMIN_PASSWORD if there is none.
    -> message describing what happened (or None if an admin already exists)."""
    if db.q(engine, "select 1 from users where role='admin' limit 1"):
        return None
    email = email or os.environ.get("ADMIN_EMAIL")
    password = password or os.environ.get("ADMIN_PASSWORD")
    if not email or not password:
        return "No admin account exists. Set ADMIN_EMAIL and ADMIN_PASSWORD (see SETUP.md) and restart."
    ok, msg = create_user(engine, email, password, "admin")
    return msg


def can(role, action):
    return action in ALLOWED.get(role, set())
