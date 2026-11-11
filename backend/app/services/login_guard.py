"""Brute-force protection for staff sign-in.

* Per account: 5 failed passwords within 15 minutes lock the account for 15 minutes.
* Per client IP: 20 failed attempts within 15 minutes are refused for 15 minutes
  (in memory, per API process; put a gateway/WAF limit in front for multi-instance deployments).
The caller always shows the same generic message, so it can't be used to discover accounts.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from app.models import User

MAX_ACCOUNT_FAILURES = 5
MAX_IP_FAILURES = 20
WINDOW = timedelta(minutes=15)
LOCKOUT = timedelta(minutes=15)

_ip_failures: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ip_blocked(ip: str | None) -> bool:
    if not ip:
        return False
    cutoff = time.time() - WINDOW.total_seconds()
    with _lock:
        q = _ip_failures[ip]
        while q and q[0] < cutoff:
            q.popleft()
        return len(q) >= MAX_IP_FAILURES


def record_ip_failure(ip: str | None) -> None:
    if ip:
        with _lock:
            _ip_failures[ip].append(time.time())


def account_locked(user: User) -> bool:
    until = _aware(user.locked_until)
    return bool(until and until > datetime.now(timezone.utc))


def record_failure(user: User) -> bool:
    """Count a failed password; returns True if this failure locked the account."""
    now = datetime.now(timezone.utc)
    first = _aware(user.first_failed_at)
    if first is None or now - first > WINDOW:
        user.first_failed_at, user.failed_logins = now, 0
    user.failed_logins = (user.failed_logins or 0) + 1
    if user.failed_logins >= MAX_ACCOUNT_FAILURES:
        user.locked_until = now + LOCKOUT
        user.failed_logins, user.first_failed_at = 0, None
        return True
    return False


def record_success(user: User) -> None:
    user.failed_logins, user.first_failed_at, user.locked_until = 0, None, None
    user.last_login_at = datetime.now(timezone.utc)


def reset_ip_failures() -> None:  # tests
    with _lock:
        _ip_failures.clear()
