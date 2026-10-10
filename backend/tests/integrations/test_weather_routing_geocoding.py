"""External services with httpx.MockTransport: no real network calls in tests."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.core.config import Settings
from app.integrations.geocoding import LOCATION_UNKNOWN, Geocoder
from app.integrations.routing import RoutingService
from app.integrations.weather import SimulationWeather, WeatherService, describe, parse_open_meteo
from app.models.schemas import GeoPoint

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
ZONE_C = GeoPoint(lat=12.979, lng=80.181)


def cfg(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def mock_http(handler) -> tuple[httpx.Client, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(record)), seen


def open_meteo_body(rain=12.0, wind=30.0, code=63, hourly_rain=(14, 22, 18, 12, 10, 8)):
    return {
        "current": {"temperature_2m": 25.5, "precipitation": rain, "rain": rain, "wind_speed_10m": wind, "weather_code": code},
        "hourly": {"time": [f"t{i}" for i in range(6)], "precipitation": list(hourly_rain), "wind_speed_10m": [wind] * 6},
    }


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


# ---------------------------------------------------------------- weather

def test_parse_open_meteo_derives_condition_and_severe_flag():
    r = parse_open_meteo("Zone C", open_meteo_body(), T0)
    assert (r.condition, r.rainfall_mm_per_hr, r.source) == ("Heavy rain", 12.0, "open-meteo")
    assert r.severe_warning  # forecast peak 22 >= 20 mm/h
    assert "not an official warning" in r.forecast_summary
    assert r.facts().forecast_max_mm_per_hr == 22
    calm = parse_open_meteo("Zone C", open_meteo_body(rain=0, code=2, hourly_rain=(0,) * 6), T0)
    assert (calm.condition, calm.severe_warning) == ("Partly cloudy", False)


@pytest.mark.parametrize("rain, code, text", [(10, None, "Heavy rain"), (2.5, None, "Moderate rain"),
                                              (0.2, None, "Light rain"), (0, 95, "Thunderstorm"), (0, None, "No rain")])
def test_describe(rain, code, text):
    assert describe(rain, code) == text


def test_live_request_parameters_and_cache():
    http, seen = mock_http(lambda r: httpx.Response(200, json=open_meteo_body()))
    clock = Clock()
    svc = WeatherService(cfg(weather_mode="live"), http=http, clock=clock)
    svc.get("Zone C", ZONE_C)
    q = seen[0].url.params
    assert (q["latitude"], q["longitude"], q["forecast_hours"]) == ("12.979", "80.181", "6")
    assert "precipitation" in q["current"] and q["hourly"] == "precipitation,wind_speed_10m"

    clock.now = T0 + timedelta(minutes=9)
    svc.get("Zone C", ZONE_C)
    assert len(seen) == 1  # served from the 10-minute cache
    clock.now = T0 + timedelta(minutes=11)
    svc.get("Zone C", ZONE_C)
    assert len(seen) == 2


def test_failure_returns_last_good_reading_marked_stale():
    responses = iter([httpx.Response(200, json=open_meteo_body()), httpx.Response(503)])
    http, _ = mock_http(lambda r: next(responses))
    clock = Clock()
    svc = WeatherService(cfg(weather_mode="live"), http=http, clock=clock)
    first = svc.get("Zone C", ZONE_C)
    clock.now = T0 + timedelta(minutes=30)
    second = svc.get("Zone C", ZONE_C)
    assert second.stale and second.source == "open-meteo" and second.fetched_at == first.fetched_at
    assert "HTTPStatusError" in second.fallback_reason


def test_failure_without_history_uses_labelled_simulation():
    def boom(request):
        raise httpx.ConnectTimeout("timeout")

    http, _ = mock_http(boom)
    r = WeatherService(cfg(weather_mode="live"), http=http).get("Zone C", ZONE_C)
    assert r.source == "simulation" and "ConnectTimeout" in r.fallback_reason and r.rainfall_mm_per_hr == 24


def test_simulation_mode_never_calls_the_network_and_accepts_injected_changes():
    http, seen = mock_http(lambda r: httpx.Response(500))
    sim = SimulationWeather()
    svc = WeatherService(cfg(weather_mode="simulation"), http=http, simulation=sim)
    assert svc.get("Zone A", ZONE_C).rainfall_mm_per_hr == 18
    sim.set(sim.get("Zone A").model_copy(update={"rainfall_mm_per_hr": 30.0, "wind_kph": 52.0}))
    assert svc.get("Zone A", ZONE_C).wind_kph == 52
    assert svc.get("Zone Q", ZONE_C) is None and seen == []


# ---------------------------------------------------------------- routing

A, B = GeoPoint(lat=13.04, lng=80.25), GeoPoint(lat=13.035, lng=80.209)


def test_route_uses_lng_lat_order_and_parses_result():
    http, seen = mock_http(lambda r: httpx.Response(200, json={"code": "Ok", "routes": [{"distance": 5400, "duration": 720}]}))
    t = RoutingService(cfg(routing_mode="live"), http=http).route(A, B)
    assert (t.distance_km, t.minutes, t.source) == (5.4, 12.0, "osrm")
    assert "/route/v1/driving/80.250000,13.040000;80.209000,13.035000" in str(seen[0].url)


@pytest.mark.parametrize("response", [httpx.Response(500), httpx.Response(200, json={"code": "NoRoute", "routes": []})])
def test_route_failure_falls_back_to_labelled_estimate(response):
    http, _ = mock_http(lambda r: response)
    svc = RoutingService(cfg(routing_mode="live"), http=http)
    t = svc.route(A, B)
    assert t.source == "estimated" and t.minutes > 0 and "estimated" in svc.last_error


def test_table_fills_cells_and_keeps_estimates_for_null_cells():
    body = {"code": "Ok", "durations": [[600, None]], "distances": [[5000, None]]}
    http, seen = mock_http(lambda r: httpx.Response(200, json=body))
    out = RoutingService(cfg(routing_mode="live"), http=http).table({"BOAT-002": A}, {"INC-002": B, "INC-003": ZONE_C})
    assert out[("BOAT-002", "INC-002")].source == "osrm" and out[("BOAT-002", "INC-002")].minutes == 10
    assert out[("BOAT-002", "INC-003")].source == "estimated"
    q = seen[0].url.params
    assert (q["sources"], q["destinations"]) == ("0", "1;2")


def test_simulation_routing_is_offline():
    http, seen = mock_http(lambda r: httpx.Response(500))
    out = RoutingService(cfg(routing_mode="simulation"), http=http).table({"X": A}, {"Y": B})
    assert out[("X", "Y")].source == "estimated" and seen == []


# ---------------------------------------------------------------- geocoding

@pytest.fixture
def geo():
    return Geocoder(cfg())


def test_explicit_coordinates_are_reported_and_mapped_to_zone(geo):
    r = geo.resolve(lat=13.0362, lng=80.2075)
    assert (r.zone, r.provenance, r.method) == ("Zone B", "reported", "coordinates")


def test_zone_name_resolves_to_centre_as_inferred(geo):
    r = geo.resolve(zone="zone d")
    assert (r.zone, r.location, r.provenance) == ("Zone D", GeoPoint(lat=13.065, lng=80.235), "inferred")
    assert r.verification_needed


def test_alias_in_text(geo):
    assert geo.resolve(text="water flooding on River Street, send help").zone == "Zone D"


def test_unknown_location_is_never_guessed(geo):
    r = geo.resolve(text="people need help somewhere near the old temple")
    assert r.location is None and r.provenance is None and r.verification_needed == [LOCATION_UNKNOWN]


def test_nominatim_is_off_by_default(geo):
    http, seen = mock_http(lambda r: httpx.Response(200, json=[{"lat": "1", "lon": "2"}]))
    geo.http = http
    assert geo.resolve(location_text="10 Downing Street").location is None and seen == []


def test_nominatim_when_enabled_sends_user_agent():
    http, seen = mock_http(lambda r: httpx.Response(200, json=[{"lat": "13.065", "lon": "80.236"}]))
    g = Geocoder(cfg(geocoding_nominatim=True), http=http)
    r = g.resolve(location_text="Some Street")
    assert (r.method, r.provenance, r.zone) == ("nominatim", "inferred", "Zone D")
    assert "disaster-response-poc" in seen[0].headers["User-Agent"]
