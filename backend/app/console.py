"""Interactive database console, the equivalent of `rails console`.

    cd backend && source .venv/bin/activate && python -m app.console

Preloaded: `db` (a SQLAlchemy session), every model (Incident, Resource, Action, ...), `select`, `func`,
and ActiveRecord-style helpers:

    Rails (ActiveRecord)                     This console
    Incident.all                             all(Incident)
    Incident.find("INC-003")                 find(Incident, "INC-003")
    Incident.where(zone: "Zone C")           where(Incident, zone="Zone C")
    Incident.where(zone: "Zone C").first     where(Incident, zone="Zone C")[0]
    Incident.count                           count(Incident)
    Incident.order(reported_at: :desc)       db.scalars(select(Incident).order_by(Incident.reported_at.desc())).all()
    incident.attributes                      show(incident)
    Resource.where(status: "available")      table(where(Resource, status="available"))
    incident.update(status: "monitoring")    inc.status = "monitoring"; db.commit()

Changes are only saved after db.commit(); db.rollback() undoes uncommitted ones.
"""

import code
from typing import Any

from sqlalchemy import func, inspect, select, text

from app.db import models as m
from app.db.geo import geom_to_path, geom_to_point
from app.db.session import Base, SessionLocal

db = SessionLocal()
MODELS = {mapper.class_.__name__: mapper.class_ for mapper in Base.registry.mappers}


def _value(obj: Any, attr: str) -> Any:
    value = getattr(obj, attr)
    if attr == "geom" and value is not None:  # PostGIS geometry -> readable lat/lng
        try:
            point = geom_to_point(value)
            return {"lat": point.lat, "lng": point.lng}
        except Exception:  # noqa: BLE001 - linestring (roads)
            return [{"lat": p.lat, "lng": p.lng} for p in geom_to_path(value)]
    return value


def attrs(obj: Any) -> dict[str, Any]:
    """All column values of a row as a dict (like `record.attributes`)."""
    return {c.key: _value(obj, c.key) for c in inspect(obj).mapper.column_attrs}


def show(obj: Any) -> None:
    """Pretty-print one row, one column per line."""
    for key, value in attrs(obj).items():
        print(f"{key:>24}: {value}")


def all(model: type, limit: int | None = None) -> list:  # noqa: A001 - mirrors Model.all
    query = select(model)
    pk = inspect(model).primary_key[0]
    return db.scalars(query.order_by(pk).limit(limit)).all()


def find(model: type, id_: Any) -> Any:
    """Row by primary key, or None (like `Model.find_by(id:)`)."""
    return db.get(model, id_)


def where(model: type, **filters: Any) -> list:
    query = select(model).filter_by(**filters)
    return db.scalars(query.order_by(inspect(model).primary_key[0])).all()


def count(model: type, **filters: Any) -> int:
    return db.scalar(select(func.count()).select_from(model).filter_by(**filters))


def table(rows: list, columns: list[str] | None = None, width: int = 28) -> None:
    """Print rows as a simple table (default: the first 6 columns)."""
    if not rows:
        print("(no rows)")
        return
    cols = columns or [c.key for c in inspect(rows[0]).mapper.column_attrs if c.key != "geom"][:6]
    print(" | ".join(f"{c[:width]:<{width}}" for c in cols))
    print("-+-".join("-" * width for _ in cols))
    for r in rows:
        print(" | ".join(f"{str(_value(r, c))[:width]:<{width}}" for c in cols))
    print(f"({len(rows)} rows)")


def sql(statement: str, **params: Any) -> list:
    """Run raw SQL and return the rows, e.g. sql("select id, status from actions where status = :s", s="pending")."""
    return db.execute(text(statement), params).all()


BANNER = f"""Disaster Response database console (like `rails console`)
Connected to: {db.get_bind().url.render_as_string(hide_password=True)}
Models: {', '.join(sorted(MODELS))}
Try:  count(Incident)   all(Incident)   show(find(Incident, "INC-003"))   table(where(Resource, status="available"))
      sql("select id, status, resource_ids from actions order by id")   (save changes with db.commit())
"""

NAMESPACE = {"db": db, "m": m, "select": select, "func": func, "text": text, "attrs": attrs, "show": show,
             "all": all, "find": find, "where": where, "count": count, "table": table, "sql": sql, **MODELS}

if __name__ == "__main__":
    try:
        import readline  # noqa: F401 - arrow-key history in the console
    except ImportError:
        pass
    code.interact(banner=BANNER, local=NAMESPACE, exitmsg="Bye. (Uncommitted changes were discarded.)")
    db.rollback()
    db.close()
