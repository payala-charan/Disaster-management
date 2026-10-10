"""Create the PostGIS extension and all tables.

Usage:  python -m app.db.init_db [--drop]

The POC uses `Base.metadata.create_all` instead of migrations. create_all only creates tables that
do not exist yet; it never alters existing ones, so after changing a model run with --drop
(or `python -m app.db.seed --reset`). A production system would use Alembic migrations instead.
"""

import argparse

from sqlalchemy import text

from app.db import models  # noqa: F401  (importing registers every table on Base.metadata)
from app.db.session import Base, engine


def init_db(drop: bool = False) -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    if drop:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drop", action="store_true", help="drop all tables first (destroys data)")
    args = parser.parse_args()
    init_db(drop=args.drop)
    print(f"Created {len(Base.metadata.tables)} tables: {', '.join(sorted(Base.metadata.tables))}")
