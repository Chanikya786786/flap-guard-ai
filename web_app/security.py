"""
Security & Governance Controls for FlapGuard AI:
- Cryptographic session CSRF protection (forms + AJAX headers)
- Role-Based Access Control (Surgeon, Admin, Resident, Nurse)
- Server-side mobile token revocation (jti blacklist + user token_version)
- Sliding-window login brute-force rate limiter
- Append-only clinical & security AuditLog writer
"""

import hmac
import os
import secrets
import threading
import time
from collections import deque
from functools import wraps
from typing import Iterable, Optional, Tuple

from flask import current_app, flash, jsonify, redirect, request, session, url_for
from flask_login import current_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from datetime import timedelta, timezone
from web_app.models import AuditLog, RevokedToken, User, db, utc_now


PRIVILEGED_ROLES = frozenset({"Surgeon", "Admin"})
ENROLLMENT_ROLES = frozenset({"Surgeon", "Admin", "Resident"})
ALL_CLINICAL_ROLES = frozenset({"Surgeon", "Admin", "Resident", "Nurse"})

_RATE_LOCK = threading.Lock()
_LOGIN_FAILURES = {}


def get_client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    return (forwarded or request.remote_addr or "unknown")[:64]


# ---------------------------------------------------------------------------
# CSRF Protection
# ---------------------------------------------------------------------------
def get_csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def is_csrf_enabled() -> bool:
    explicit = current_app.config.get("CSRF_ENABLED")
    if explicit is not None:
        return bool(explicit)
    return not bool(current_app.config.get("TESTING", False))


def validate_csrf_request() -> bool:
    expected = session.get("_csrf_token")
    if not expected:
        return False
    provided = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
    if not provided:
        return False
    return hmac.compare_digest(str(expected), str(provided))


# ---------------------------------------------------------------------------
# Login Brute-Force Rate Limiting
# ---------------------------------------------------------------------------
def _rate_limit_settings() -> Tuple[bool, int, int]:
    explicit = current_app.config.get("RATE_LIMIT_ENABLED")
    enabled = bool(explicit) if explicit is not None else not bool(current_app.config.get("TESTING", False))
    limit = int(current_app.config.get("LOGIN_RATE_LIMIT", 15))
    window = int(current_app.config.get("LOGIN_RATE_WINDOW_SEC", 300))
    return enabled, limit, window


def is_login_rate_limited(ip_addr: str, email: str) -> bool:
    enabled, limit, window = _rate_limit_settings()
    if not enabled:
        return False
    now = time.monotonic()
    key = (ip_addr, email.strip().lower())
    with _RATE_LOCK:
        bucket = _LOGIN_FAILURES.get(key)
        if not bucket:
            return False
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        return len(bucket) >= limit


def record_login_failure(ip_addr: str, email: str) -> None:
    enabled, _, window = _rate_limit_settings()
    if not enabled:
        return
    now = time.monotonic()
    key = (ip_addr, email.strip().lower())
    with _RATE_LOCK:
        bucket = _LOGIN_FAILURES.setdefault(key, deque())
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        bucket.append(now)


def clear_login_failures(ip_addr: str, email: str) -> None:
    key = (ip_addr, email.strip().lower())
    with _RATE_LOCK:
        _LOGIN_FAILURES.pop(key, None)


def reset_rate_limiter() -> None:
    with _RATE_LOCK:
        _LOGIN_FAILURES.clear()


# ---------------------------------------------------------------------------
# Registration Invite Gate (Optional in Dev/CI, Enforced When Configured)
# ---------------------------------------------------------------------------
def get_required_invite_code() -> str:
    cfg = current_app.config.get("REGISTRATION_INVITE_CODE")
    if cfg is not None:
        return str(cfg).strip()
    return os.environ.get("REGISTRATION_INVITE_CODE", "").strip()


def validate_invite_code(provided_code: str) -> bool:
    required = get_required_invite_code()
    if not required:
        return True
    return hmac.compare_digest(required, str(provided_code or "").strip())


# ---------------------------------------------------------------------------
# Mobile Bearer Tokens with JTI Revocation + Token Version
# ---------------------------------------------------------------------------
def _token_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="flapguard-mobile-auth")


def generate_mobile_token(user: User) -> str:
    payload = {
        "user_id": user.id,
        "email": user.email,
        "jti": secrets.token_hex(16),
        "tv": int(user.token_version or 1),
    }
    return _token_serializer().dumps(payload)


def verify_mobile_token_with_meta(token: str) -> Tuple[Optional[User], Optional[str]]:
    try:
        payload = _token_serializer().loads(token, max_age=86400 * 7)
        user_id = int(payload["user_id"])
        jti = str(payload.get("jti", ""))
        token_tv = int(payload.get("tv", 1))
    except (BadSignature, SignatureExpired, KeyError, ValueError, TypeError):
        return None, None

    if jti and RevokedToken.query.filter_by(jti=jti).first() is not None:
        return None, None

    user = db.session.get(User, user_id)
    if user is None or enforce_due_account_deletion(user):
        return None, None
    if user.scheduled_deletion_at is not None:
        return None, None
    if int(user.token_version or 1) != token_tv:
        return None, None

    return user, jti


def verify_mobile_token(token: str) -> Optional[User]:
    user, _ = verify_mobile_token_with_meta(token)
    return user


def revoke_mobile_token(jti: Optional[str], user_id: Optional[int] = None) -> bool:
    if not jti:
        return False
    if RevokedToken.query.filter_by(jti=jti).first() is not None:
        return True
    db.session.add(RevokedToken(jti=jti[:64], user_id=user_id))
    db.session.commit()
    return True


# ---------------------------------------------------------------------------
# Append-Only Audit Trail
# ---------------------------------------------------------------------------
def log_audit(
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    details: str = "",
    actor: Optional[User] = None,
) -> None:
    try:
        if actor is None:
            if hasattr(request, "mobile_user") and request.mobile_user is not None:
                actor = request.mobile_user
            elif current_user and getattr(current_user, "is_authenticated", False):
                actor = current_user

        entry = AuditLog(
            actor_id=actor.id if actor else None,
            actor_email=(actor.email if actor else "anonymous")[:150],
            actor_role=(actor.role if actor else "Anonymous")[:40],
            action=str(action)[:60],
            resource_type=str(resource_type)[:50],
            resource_id=str(resource_id)[:60],
            details=str(details)[:400],
            ip_address=get_client_ip(),
        )
        db.session.add(entry)
        db.session.commit()
    except Exception:
        db.session.rollback()


# ---------------------------------------------------------------------------
# Role-Based Access Control Decorators
# ---------------------------------------------------------------------------
def web_roles_required(allowed_roles: Iterable[str]):
    allowed = frozenset(allowed_roles)

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("main.login"))
            if current_user.role not in allowed:
                log_audit(
                    "RBAC_DENIED",
                    resource_type="web_route",
                    resource_id=request.path,
                    details=f"Role '{current_user.role}' denied; requires {sorted(allowed)}",
                    actor=current_user,
                )
                if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify({"error": "Insufficient role permissions for this action."}), 403
                flash("Your clinical role does not have permission for this action.", "danger")
                return redirect(url_for("main.dashboard")), 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def mobile_roles_required(allowed_roles: Iterable[str]):
    allowed = frozenset(allowed_roles)

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = getattr(request, "mobile_user", None)
            if user is None:
                return jsonify({"error": "Authentication required."}), 401
            if user.role not in allowed:
                log_audit(
                    "RBAC_DENIED",
                    resource_type="mobile_route",
                    resource_id=request.path,
                    details=f"Role '{user.role}' denied; requires {sorted(allowed)}",
                    actor=user,
                )
                return jsonify({"error": "Insufficient role permissions for this clinical action."}), 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# 30-Day Scheduled Account Deletion & Automatic Clinical-Safe PII Purge
# ---------------------------------------------------------------------------
ACCOUNT_DELETION_GRACE_DAYS = 30


def normalize_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def purge_user_pii(user: User) -> None:
    """
    Permanently erase personal identity fields while keeping relational
    clinical charts, readings, outcomes, and audit trails intact.
    """
    if user.is_deleted:
        return
    old_email = user.email
    user.full_name = f"Deleted Clinician #{user.id}"
    user.email = f"deleted-user-{user.id}@anonymized.local"
    user.profile_photo = None
    user.set_password(secrets.token_urlsafe(32))
    user.is_deleted = True
    user.bump_token_version()
    db.session.commit()
    log_audit(
        "ACCOUNT_PURGED",
        "user",
        str(user.id),
        f"30-day grace expired; PII anonymized (prior={old_email}); clinical records preserved",
    )


def enforce_due_account_deletion(user: Optional[User]) -> bool:
    """Purge a single user if their 30-day scheduled deletion timestamp has passed. Returns True if deleted."""
    if user is None:
        return False
    if user.is_deleted:
        return True
    deadline = normalize_utc(user.scheduled_deletion_at)
    if deadline is not None and deadline <= utc_now():
        purge_user_pii(user)
        return True
    return False


def purge_all_expired_accounts() -> int:
    """Sweep any accounts whose 30-day deletion deadline has elapsed."""
    now = utc_now()
    candidates = User.query.filter(
        User.is_deleted.is_(False),
        User.scheduled_deletion_at.isnot(None),
    ).all()
    purged = 0
    for u in candidates:
        dl = normalize_utc(u.scheduled_deletion_at)
        if dl is not None and dl <= now:
            purge_user_pii(u)
            purged += 1
    return purged


def schedule_user_deletion(user: User, grace_days: int = ACCOUNT_DELETION_GRACE_DAYS):
    """Mark account for permanent deletion after `grace_days` and invalidate all active tokens."""
    now = utc_now()
    deadline = now + timedelta(days=grace_days)
    user.deletion_requested_at = now
    user.scheduled_deletion_at = deadline
    user.bump_token_version()
    db.session.commit()
    return deadline


def remaining_deletion_window(user: User) -> Tuple[int, int]:
    """Return (days_remaining, hours_remaining) until scheduled deletion."""
    deadline = normalize_utc(user.scheduled_deletion_at)
    if deadline is None:
        return 0, 0
    delta = deadline - utc_now()
    total_hours = max(0, int(delta.total_seconds() // 3600))
    return total_hours // 24, total_hours % 24
