"""Grant (or restore) web-UI admin access for one operator, directly in the DB.

Operators live only in the ``users`` table — there is no env-var allowlist. Normally
the first Google sign-in on an empty table bootstraps an admin and everyone else is
approved from /settings/users. This script is the recovery path for the one case that
leaves nobody able to log in: a populated ``users`` table with no approved admin
(e.g. the last admin was revoked, or rows were seeded before this policy).

Usage (PowerShell):
    .\\.venv\\Scripts\\python.exe scripts\\bootstrap_admin.py ronald@estsoft.com
    .\\.venv\\Scripts\\python.exe scripts\\bootstrap_admin.py --list

The email must be on ALLOWED_EMAIL_DOMAIN; pass --force to override that check.
Idempotent: re-running on an existing row just re-approves and re-promotes it.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.config import settings  # noqa: E402
from src.common.logging import setup_logging  # noqa: E402
from src.db.models import User  # noqa: E402
from src.db.session import SessionLocal  # noqa: E402

setup_logging()
logger = logging.getLogger(__name__)


def _list_users() -> int:
    with SessionLocal() as session:
        rows = session.query(User).order_by(User.email).all()
        if not rows:
            print("users table is empty — the next Google sign-in bootstraps an admin.")
            return 0
        width = max(len(r.email) for r in rows)
        for r in rows:
            flag = "approved" if r.approved else "PENDING "
            print(f"  {r.email:<{width}}  {flag}  role={r.role or '-'}")
        admins = [r for r in rows if r.approved and r.role == "admin"]
        print(f"\n{len(rows)} user(s), {len(admins)} approved admin(s).")
        if not admins:
            print("WARNING: no approved admin — nobody can manage operators in the UI.")
    return 0


def main() -> int:
    # Plain-ASCII help text — argparse writes it straight to a cp949 console here.
    parser = argparse.ArgumentParser(
        description="Grant web-UI admin access to one operator, directly in the DB.",
    )
    parser.add_argument("email", nargs="?", help="operator email to promote to approved admin")
    parser.add_argument("--list", action="store_true", help="show the users table and exit")
    parser.add_argument("--force", action="store_true", help="skip the ALLOWED_EMAIL_DOMAIN check")
    args = parser.parse_args()

    if args.list or not args.email:
        if not args.email and not args.list:
            parser.print_usage()
            print("\nERROR: provide an email, or --list.", file=sys.stderr)
            return 2
        return _list_users()

    email = args.email.strip().lower()
    domain = (settings.ALLOWED_EMAIL_DOMAIN or "").strip().lower()
    if domain and not args.force and not email.endswith("@" + domain):
        print(
            f"ERROR: {email} is not on ALLOWED_EMAIL_DOMAIN ({domain}); "
            "Google sign-in would reject it. Use --force only if you changed the domain.",
            file=sys.stderr,
        )
        return 2

    with SessionLocal() as session:
        user = session.get(User, email)
        created = user is None
        if user is None:
            user = User(email=email)
            session.add(user)
        user.approved = True
        user.role = "admin"
        if created:
            user.created_at = datetime.now(timezone.utc)
        session.commit()

    print(f"{'Created' if created else 'Updated'} {email} → role=admin, approved=True")
    print("Sign in at /auth/login with that Google account.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
