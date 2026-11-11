"""Create tables and (optionally) load reference + demo data.

    python -m scripts.init_db            # create tables + reference data (staff, payers, providers)
    python -m scripts.init_db --demo     # ... plus fictional demo patients and appointments
    python -m scripts.init_db --reset --demo   # drop everything first (dev only!)
"""
import argparse

from app.db import Base, SessionLocal, create_all, engine
from app.seed import seed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="load fictional demo patients/appointments")
    ap.add_argument("--reset", action="store_true", help="DROP all tables first (dev only)")
    args = ap.parse_args()
    if args.reset:
        from app import models  # noqa: F401
        Base.metadata.drop_all(engine)
        print("Dropped all tables")
    create_all()
    with SessionLocal() as db:
        seed(db, demo=args.demo)
    print(f"Database ready on {engine.url.render_as_string(hide_password=True)}")


if __name__ == "__main__":
    main()
