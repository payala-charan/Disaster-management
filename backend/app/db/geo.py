"""Convert between PostGIS geometries and the API's {lat, lng} points.

Watch the axis order: WKT / PostGIS use (x=longitude, y=latitude), while the API and Leaflet
use lat first. Keeping every conversion in this one module avoids silently swapped coordinates.
"""

from typing import Any

from geoalchemy2.elements import WKTElement
from geoalchemy2.shape import to_shape

from app.models.schemas import GeoPoint

SRID = 4326


def point_to_geom(p: GeoPoint) -> WKTElement:
    return WKTElement(f"POINT({p.lng} {p.lat})", srid=SRID)


def path_to_geom(path: list[GeoPoint]) -> WKTElement:
    coords = ", ".join(f"{p.lng} {p.lat}" for p in path)
    return WKTElement(f"LINESTRING({coords})", srid=SRID)


def geom_to_point(geom: Any) -> GeoPoint | None:
    """DB geometry -> GeoPoint (None stays None: an unknown location is never made up)."""
    if geom is None:
        return None
    shape = to_shape(geom)
    return GeoPoint(lat=shape.y, lng=shape.x)


def geom_to_path(geom: Any) -> list[GeoPoint]:
    return [GeoPoint(lat=y, lng=x) for x, y in to_shape(geom).coords]
