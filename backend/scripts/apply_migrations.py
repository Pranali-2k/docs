"""Apply every pending Alembic revision: the same as `python -m alembic upgrade head`.

    python -m scripts.apply_migrations     # uses MIGRATION_DATABASE_URL if set, else DATABASE_URL (PostgreSQL)
"""
import sys

from app.db import migrate_to_head


def main() -> int:
    migrate_to_head()
    print("database is at the latest revision")
    return 0


if __name__ == "__main__":
    sys.exit(main())
