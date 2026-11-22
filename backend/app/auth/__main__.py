"""Server-side sign-in commands: the way back in when the password is lost.

    python -m app.auth set-password      set or reset the password; signs everyone out
    python -m app.auth sign-out-all      end every browser session
    python -m app.auth revoke-tokens     revoke every API token

In Docker:  docker compose exec -it backend python -m app.auth set-password
Run it where the backend runs, with the same environment (it uses the same
metadata database). It works whether SFS is running or not.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys


async def _set_password(from_stdin: bool) -> int:
    from app.auth import store
    from app.auth.hashing import check_new_password, hash_password
    from app.auth.password import PRINCIPAL_ID, SECRET_NAME
    if os.getenv("SFS_PASSWORD"):
        print("SFS_PASSWORD is set, and it wins at every start: change it there instead.", file=sys.stderr)
        return 1
    if from_stdin:
        pw = sys.stdin.readline().rstrip("\n")
    else:
        pw = getpass.getpass("New SFS password: ")
        if getpass.getpass("Again: ") != pw:
            print("The two don't match; nothing changed.", file=sys.stderr)
            return 1
    try:
        check_new_password(pw)
    except ValueError as e:
        print(f"{e} Nothing changed.", file=sys.stderr)
        return 1
    await store.set_secret(SECRET_NAME, hash_password(pw))
    n = await store.end_sessions(PRINCIPAL_ID)
    print(f"Password set. {n} browser session(s) signed out.")
    return 0


async def _main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="python -m app.auth", description="SFS sign-in commands")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("set-password", help="set or reset the instance password")
    sp.add_argument("--stdin", action="store_true", help="read the password from standard input")
    sub.add_parser("sign-out-all", help="end every browser session")
    sub.add_parser("revoke-tokens", help="revoke every API token")
    args = p.parse_args(argv)

    from app.core.database import init_db
    await init_db()
    from app.auth import store
    if args.cmd == "set-password":
        return await _set_password(args.stdin)
    if args.cmd == "sign-out-all":
        print(f"{await store.end_sessions()} session(s) ended.")
        return 0
    tokens = await store.list_tokens()
    for t in tokens:
        await store.revoke_token(t["id"])
    print(f"{len(tokens)} token(s) revoked.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
