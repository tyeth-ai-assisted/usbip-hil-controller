#!/usr/bin/env python3
"""
Mint a new HIL controller bearer token and insert it into the SQLite DB.

Usage:
    python scripts/mint-token.py --db /var/lib/hil/jobs.db \
        --label "ws-python-ci" --pool wippersnapper-python

Prints the plain token once (format: hil_<id>_<secret>).
The DB stores only the argon2id hash; the plain token is never logged.
"""

import argparse
import asyncio
import secrets
import sys
import uuid
from datetime import datetime, timezone


def main() -> None:
    p = argparse.ArgumentParser(description="Mint a HIL API token")
    p.add_argument("--db", required=True, help="Path to the SQLite DB")
    p.add_argument("--label", required=True, help="Human label for this token")
    p.add_argument("--pool", default="public", help="Device pool this token may target")
    p.add_argument("--repo", default="", help="Pin token to a specific repo (owner/name)")
    args = p.parse_args()

    asyncio.run(_mint(args))


async def _mint(args: argparse.Namespace) -> None:
    try:
        from argon2 import PasswordHasher
    except ImportError:
        print("ERROR: argon2-cffi not installed. Run: pip install argon2-cffi", file=sys.stderr)
        sys.exit(1)

    try:
        import aiosqlite
    except ImportError:
        print("ERROR: aiosqlite not installed. Run: pip install aiosqlite", file=sys.stderr)
        sys.exit(1)

    token_id = secrets.token_urlsafe(8)
    secret = secrets.token_urlsafe(32)
    plain_token = f"hil_{token_id}_{secret}"

    ph = PasswordHasher()
    hashed = ph.hash(secret)

    created_at = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(args.db) as db:
        await db.execute(
            """
            INSERT INTO tokens (id, label, repo, pool, hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (token_id, args.label, args.repo, args.pool, hashed, created_at),
        )
        await db.commit()

    print(plain_token)
    print(f"\nToken ID : {token_id}")
    print(f"Label    : {args.label}")
    print(f"Pool     : {args.pool}")
    print(f"Repo pin : {args.repo or '(any)'}")
    print("\nStore this token in your CI secret HIL_API_TOKEN. It will not be shown again.")


if __name__ == "__main__":
    main()
