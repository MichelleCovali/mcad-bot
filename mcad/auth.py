"""Passwords, expiring invitations and revocable browser sessions."""
import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from threading import BoundedSemaphore

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .auth_models import AdminAuthAttempt, AdminInvite, AdminSession, AdminUser
from .models import utcnow, uuid
from .service import iso


PASSWORD_MIN = 12
PASSWORD_MAX_BYTES = 256
SESSION_SECONDS = 12 * 60 * 60
INVITE_HOURS = 72
ATTEMPT_WINDOW = timedelta(minutes=10)
ROLES = frozenset({"admin", "reviewer"})
EMAIL_RE = re.compile(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", re.ASCII)
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}", re.ASCII)
HASH_SLOTS = BoundedSemaphore(4)
# Match the work done for known users even when an email isn't registered.
DUMMY_PASSWORD_HASH = "scrypt-v1$" + "00" * 16 + "$" + "00" * 32


class InvalidCredentials(Exception):
    pass


class TooManyAttempts(Exception):
    pass


class InvalidInvite(Exception):
    pass


class AccountConflict(Exception):
    pass


def normalize_email(value):
    email = value.strip().lower() if isinstance(value, str) else ""
    if len(email) > 254 or not EMAIL_RE.fullmatch(email) or len(email.partition("@")[0]) > 64:
        raise ValueError("Enter a valid email address.")
    return email


def password_bytes(value):
    if isinstance(value, str) and len(value) <= PASSWORD_MAX_BYTES:
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError:
            return None
        if len(encoded) <= PASSWORD_MAX_BYTES:
            return encoded
    return None


def validate_new_password(value):
    encoded = password_bytes(value)
    if encoded is None or len(value) < PASSWORD_MIN or not value.strip():
        raise ValueError(f"Use a password with at least {PASSWORD_MIN} characters and at most {PASSWORD_MAX_BYTES} UTF-8 bytes.")
    return encoded


def _derive_password(password, salt):
    # OpenSSL's default memory limit is below the memory cost of this parameter set.
    with HASH_SLOTS:
        return hashlib.scrypt(password, salt=salt, n=2**15, r=8, p=3, maxmem=64 * 1024 * 1024, dklen=32)


def hash_password(password):
    encoded = validate_new_password(password)
    salt = secrets.token_bytes(16)
    digest = _derive_password(encoded, salt)
    return f"scrypt-v1${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    encoded = password_bytes(password)
    if not encoded:
        return False
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        salt, digest = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
    except (AttributeError, ValueError):
        return False
    return (scheme == "scrypt-v1" and len(salt) == 16 and len(digest) == 32
            and hmac.compare_digest(_derive_password(encoded, salt), digest))


def token_hash(value):
    if not isinstance(value, str) or not TOKEN_RE.fullmatch(value):
        return None
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def user_dict(user):
    return {"id": user.id, "email": user.email, "role": user.role, "active": user.active,
            "createdAt": iso(user.created_at)}


def invite_dict(invite, now):
    if invite.accepted_at:
        status = "accepted"
    elif invite.revoked_at:
        status = "revoked"
    else:
        status = "expired" if invite.expires_at <= now else "pending"
    return {"id": invite.id, "email": invite.email, "role": invite.role, "status": status,
            "expiresAt": iso(invite.expires_at), "createdAt": iso(invite.created_at)}


def _dialect_insert(session, model):
    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    return insert(model)


def _consume_attempt(session, label, limit, now):
    key = hashlib.sha256(label.encode("utf-8")).hexdigest()
    expires_at = now + ATTEMPT_WINDOW
    insert = _dialect_insert(session, AdminAuthAttempt).values(key=key, attempts=1, expires_at=expires_at)
    bucket = AdminAuthAttempt
    count = session.scalar(insert.on_conflict_do_update(index_elements=[bucket.key], set_={
        "attempts": case((bucket.expires_at <= now, 1), else_=bucket.attempts + 1),
        "expires_at": case((bucket.expires_at <= now, expires_at), else_=bucket.expires_at),
    }, where=or_(bucket.expires_at <= now, bucket.attempts < limit)).returning(bucket.attempts))
    return count is not None


def _allow_attempt(factory, purpose, address, email=None):
    now = utcnow()
    with factory.begin() as session:
        session.execute(delete(AdminAuthAttempt).where(AdminAuthAttempt.expires_at <= now))
        allowed = _consume_attempt(session, f"{purpose}:address:{address}", 60, now)
        if allowed and email:
            allowed = _consume_attempt(session, f"{purpose}:email:{email}", 10, now)
    if not allowed:
        raise TooManyAttempts


def get_session_user(factory, raw_token):
    digest = token_hash(raw_token)
    if not digest:
        return None
    with factory() as session:
        user = session.scalar(select(AdminUser).join(AdminSession).where(
            AdminSession.token_hash == digest, AdminSession.expires_at > utcnow(), AdminUser.active.is_(True)
        ))
        return user_dict(user) if user else None


def revoke_session(factory, raw_token):
    digest = token_hash(raw_token)
    if digest:
        with factory.begin() as session:
            session.execute(delete(AdminSession).where(AdminSession.token_hash == digest))


def _new_session(session, user, previous_token, now):
    previous_hash = token_hash(previous_token)
    session.execute(delete(AdminSession).where(or_(AdminSession.expires_at <= now, AdminSession.token_hash == previous_hash)))
    token = secrets.token_urlsafe(32)
    session.add(AdminSession(token_hash=token_hash(token), user_id=user.id,
                             expires_at=now + timedelta(seconds=SESSION_SECONDS)))
    return user_dict(user), token


def login(factory, raw_email, password, address, previous_token=None):
    try:
        email = normalize_email(raw_email)
    except ValueError:
        _allow_attempt(factory, "login", address)
        raise InvalidCredentials from None
    _allow_attempt(factory, "login", address, email)
    with factory() as session:
        account = session.execute(select(AdminUser.id, AdminUser.password_hash).where(AdminUser.email == email)).one_or_none()
    stored = account.password_hash if account else DUMMY_PASSWORD_HASH
    if not verify_password(password, stored) or account is None:
        raise InvalidCredentials
    with factory.begin() as session:
        user = session.scalar(select(AdminUser).where(AdminUser.id == account.id).with_for_update())
        if user is None or not user.active or user.password_hash != stored:
            raise InvalidCredentials
        return _new_session(session, user, previous_token, utcnow())


def issue_invite(factory, raw_email, role):
    email = normalize_email(raw_email)
    if not isinstance(role, str) or role not in ROLES:
        raise ValueError("Choose the administrator or reviewer role.")
    now = utcnow()
    token = secrets.token_urlsafe(32)
    fields = {"id": uuid(), "email": email, "role": role, "token_hash": token_hash(token),
              "created_at": now, "expires_at": now + timedelta(hours=INVITE_HOURS),
              "accepted_at": None, "revoked_at": None}
    with factory.begin() as session:
        if session.scalar(select(AdminUser.id).where(AdminUser.email == email)):
            raise AccountConflict("An account already exists for this email address.")
        insert = _dialect_insert(session, AdminInvite).values(**fields)
        invite = session.scalar(insert.on_conflict_do_update(index_elements=[AdminInvite.email], set_={
            key: value for key, value in fields.items() if key != "email"
        }, where=AdminInvite.accepted_at.is_(None)).returning(AdminInvite))
        if invite is None:
            raise AccountConflict("An account already exists for this email address.")
        return invite_dict(invite, now), token


def _usable_invite(digest, now):
    return (AdminInvite.token_hash == digest, AdminInvite.expires_at > now,
            AdminInvite.accepted_at.is_(None), AdminInvite.revoked_at.is_(None))


def accept_invite(factory, raw_token, password, address, previous_token=None):
    _allow_attempt(factory, "accept", address)
    digest = token_hash(raw_token)
    if not digest:
        raise InvalidInvite
    with factory() as session:
        if not session.scalar(select(AdminInvite.id).where(*_usable_invite(digest, utcnow()))):
            raise InvalidInvite
    hashed = hash_password(password)
    now = utcnow()
    with factory.begin() as session:
        invite = session.scalar(update(AdminInvite).where(*_usable_invite(digest, now))
                                .values(accepted_at=now).returning(AdminInvite))
        if invite is None:
            raise InvalidInvite
        if session.scalar(select(AdminUser.id).where(AdminUser.email == invite.email)):
            raise InvalidInvite
        account = AdminUser(email=invite.email, password_hash=hashed, role=invite.role)
        session.add(account)
        session.flush()
        return _new_session(session, account, previous_token, now)


def list_team(factory):
    now = utcnow()
    with factory() as session:
        members = session.scalars(select(AdminUser).order_by(AdminUser.email))
        invites = session.scalars(select(AdminInvite).order_by(AdminInvite.created_at.desc(), AdminInvite.id))
        return {"members": [user_dict(member) for member in members], "invites": [invite_dict(invite, now) for invite in invites]}


def revoke_invite(factory, invite_id):
    now = utcnow()
    with factory.begin() as session:
        invite = session.scalar(update(AdminInvite).where(AdminInvite.id == invite_id,
            AdminInvite.accepted_at.is_(None), AdminInvite.revoked_at.is_(None),
            AdminInvite.expires_at > now).values(revoked_at=now).returning(AdminInvite))
        return invite_dict(invite, now) if invite else None


def update_member(factory, user_id, changes):
    if not changes or changes.keys() - {"role", "active"}:
        raise ValueError("Choose an active status or role to update.")
    if "active" in changes and not isinstance(changes["active"], bool):
        raise ValueError("Active status must be true or false.")
    if "role" in changes and (not isinstance(changes["role"], str) or changes["role"] not in ROLES):
        raise ValueError("Choose the administrator or reviewer role.")
    with factory.begin() as session:
        # Acquire PostgreSQL row locks in a consistent order for competing edits.
        # The conditional UPDATE also protects the invariant under SQLite's writer lock.
        users = session.scalars(select(AdminUser).where(or_(AdminUser.id == user_id, AdminUser.role == "admin"))
                                .order_by(AdminUser.id).with_for_update()).all()
        user = next((account for account in users if account.id == user_id), None)
        if user is None:
            return None
        keep_admin = changes.get("active", user.active) and changes.get("role", user.role) == "admin"
        statement = update(AdminUser).where(AdminUser.id == user_id).values(**changes)
        if not keep_admin:
            other_admins = select(func.count()).select_from(AdminUser).where(AdminUser.id != user_id,
                AdminUser.role == "admin", AdminUser.active.is_(True)).scalar_subquery()
            statement = statement.where(or_(AdminUser.active.is_(False), AdminUser.role != "admin", other_admins > 0))
        updated = session.scalar(statement.returning(AdminUser))
        if updated is None:
            raise AccountConflict("Keep at least one active administrator.")
        if not updated.active:
            session.execute(delete(AdminSession).where(AdminSession.user_id == user_id))
        return user_dict(updated)


def create_admin(factory, raw_email, password):
    email = normalize_email(raw_email)
    password_hash = hash_password(password)
    with factory.begin() as session:
        if session.scalar(select(AdminUser.id).where(AdminUser.email == email)):
            raise AccountConflict("An account already exists for this email address.")
        session.execute(update(AdminInvite).where(AdminInvite.email == email, AdminInvite.accepted_at.is_(None))
                        .values(revoked_at=utcnow()))
        session.add(AdminUser(email=email, password_hash=password_hash, role="admin"))


def reset_password(factory, raw_email, password):
    email = normalize_email(raw_email)
    password_hash = hash_password(password)
    with factory.begin() as session:
        user_id = session.scalar(update(AdminUser).where(AdminUser.email == email)
                                 .values(password_hash=password_hash).returning(AdminUser.id))
        if user_id is None:
            raise AccountConflict("No account exists for this email address.")
        session.execute(delete(AdminSession).where(AdminSession.user_id == user_id))
