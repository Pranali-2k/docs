"""Create the PostgreSQL role and database for Docs Health (run this interactively yourself).

    python -m scripts.bootstrap_db --user docshealth --db docshealth

It asks for the PostgreSQL superuser password and the new app password. Neither
is stored or logged. Afterwards set DATABASE_URL in .env and run scripts.init_db.
"""
import argparse
import getpass

import psycopg
from psycopg import sql


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=5432)
    ap.add_argument("--superuser", default="postgres")
    ap.add_argument("--user", default="docshealth")
    ap.add_argument("--db", default="docshealth")
    args = ap.parse_args()

    su_pw = getpass.getpass(f"Password for PostgreSQL superuser '{args.superuser}': ")
    app_pw = getpass.getpass(f"New password for app role '{args.user}': ")
    if not app_pw:
        raise SystemExit("App password must not be empty")

    with psycopg.connect(host=args.host, port=args.port, user=args.superuser, password=su_pw,
                         dbname="postgres", autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (args.user,)).fetchone()
        stmt = "ALTER ROLE {} WITH LOGIN PASSWORD {}" if exists else "CREATE ROLE {} WITH LOGIN PASSWORD {}"
        conn.execute(sql.SQL(stmt).format(sql.Identifier(args.user), sql.Literal(app_pw)))
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (args.db,)).fetchone():
            conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(args.db), sql.Identifier(args.user)))
    print(f"Role '{args.user}' and database '{args.db}' are ready.")
    print(f"Set in backend/.env:\n  DATABASE_URL=postgresql+psycopg://{args.user}:<password>@{args.host}:{args.port}/{args.db}")
    print("Then run:  python -m scripts.init_db --demo")


if __name__ == "__main__":
    main()
