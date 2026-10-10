"""Weather per zone: Open-Meteo (live, no API key) or simulation data.

Live request:
  GET https://api.open-meteo.com/v1/forecast?latitude=..&longitude=..
      &current=temperature_2m,precipitation,rain,wind_speed_10m,weather_code
      &hourly=precipitation,wind_speed_10m&forecast_hours=6
  (wind in km/h and precipitation in mm are Open-Meteo's defaults)

Derived values (documented thresholds, not an official warning service):
- rainfall_mm_per_hr = current precipitation (mm over the last hour)
- condition: >= 10 mm/h "Heavy rain", >= 2.5 "Moderate rain", > 0 "Light rain", otherwise from weather_code
- severe_warning = 6-h peak rain >= 20 mm/h OR wind (now or forecast) >= 60 km/h OR thunderstorm code (95/96/99).
  Open-Meteo has no official warnings, so this is a derived flag and the summary says so.

Modes and degradation:
- WEATHER_MODE=simulation: readings come from the simulation source (scenario data plus injected changes).
- live: results are cached for WEATHER_CACHE_MIN minutes per zone. On any failure the last good reading
  is returned (stale=True, keeping its original source and fetched_at); if there is none, the simulation
  reading is used. `fallback_reason` always says why, so the UI and trace can label it.
"""

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import httpx

from app.core.config import Settings, get_settings
from app.db.seed import SEED_DIR
from app.engines.models import WeatherFacts
from app.models.schemas import GeoPoint, WeatherSnapshot
from app.models.seed_data import HourlyForecast, SeedWeather

log = logging.getLogger(__name__)

HEAVY, MODERATE = 10.0, 2.5
SEVERE_RAIN_MM_PER_HR, SEVERE_WIND_KPH = 20.0, 60.0
THUNDERSTORM_CODES = {95, 96, 99}
WMO_TEXT = {0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Fog"}


class WeatherReading(WeatherSnapshot):
    forecast_hourly: list[HourlyForecast]
    stale: bool = False
    fallback_reason: str | None = None

    def snapshot(self) -> WeatherSnapshot:
        """The API-contract shape (drops internal fields)."""
        return WeatherSnapshot(**self.model_dump(include=set(WeatherSnapshot.model_fields)))

    def facts(self) -> WeatherFacts:
        return WeatherFacts.from_hourly(
            hourly=[h.precipitation_mm for h in self.forecast_hourly], zone=self.zone, condition=self.condition,
            rainfall_mm_per_hr=self.rainfall_mm_per_hr, wind_kph=self.wind_kph,
            severe_warning=self.severe_warning, source=self.source,
        )


class SimulationWeather:
    """In-memory simulation readings, seeded from data/seed/weather.json. Scenario steps call set()."""

    def __init__(self, readings: dict[str, WeatherReading] | None = None):
        self.readings = readings if readings is not None else self._from_seed()

    @staticmethod
    def _from_seed() -> dict[str, WeatherReading]:
        now = datetime.now(timezone.utc)
        raw = [SeedWeather.model_validate(w) for w in json.loads((SEED_DIR / "weather.json").read_text())]
        return {w.zone: WeatherReading(**w.model_dump(exclude={"fetched_at"}), fetched_at=w.fetched_at.resolve(now))
                for w in raw}

    def get(self, zone: str) -> WeatherReading | None:
        return self.readings.get(zone)

    def set(self, reading: WeatherReading) -> None:
        self.readings[reading.zone] = reading


def describe(rain: float, code: int | None) -> str:
    if rain >= HEAVY:
        return "Heavy rain"
    if rain >= MODERATE:
        return "Moderate rain"
    if rain > 0:
        return "Light rain"
    if code in THUNDERSTORM_CODES:
        return "Thunderstorm"
    return WMO_TEXT.get(code, "No rain") if code is not None else "No rain"


def parse_open_meteo(zone: str, data: dict, fetched_at: datetime) -> WeatherReading:
    cur, hourly = data["current"], data.get("hourly", {})
    precip = [float(p or 0) for p in hourly.get("precipitation", [])][:6]
    winds = [float(w or 0) for w in hourly.get("wind_speed_10m", [])][:6]
    forecast = [HourlyForecast(hour=i + 1, precipitation_mm=p, wind_kph=w) for i, (p, w) in enumerate(zip(precip, winds))]
    rain = float(cur.get("precipitation") or 0)
    wind = float(cur.get("wind_speed_10m") or 0)
    code = cur.get("weather_code")
    peak_rain, peak_wind = max([rain, *precip]), max([wind, *winds])
    severe = peak_rain >= SEVERE_RAIN_MM_PER_HR or peak_wind >= SEVERE_WIND_KPH or code in THUNDERSTORM_CODES
    summary = (f"Next 6 h: peak rain {max(precip, default=0):g} mm/h, max wind {max(winds, default=0):g} km/h"
               + (" (severe: derived from thresholds, not an official warning)" if severe else ""))
    return WeatherReading(
        zone=zone, condition=describe(rain, code), rainfall_mm_per_hr=rain, wind_kph=wind,
        temperature_c=float(cur.get("temperature_2m") or 0), forecast_summary=summary,
        forecast_hourly=forecast, severe_warning=severe, source="open-meteo", fetched_at=fetched_at,
    )


class WeatherService:
    def __init__(
        self,
        settings: Settings | None = None,
        http: httpx.Client | None = None,
        simulation: SimulationWeather | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.settings = settings or get_settings()
        self.http = http or httpx.Client(timeout=self.settings.http_timeout_s)
        self.simulation = simulation or SimulationWeather()
        self.clock = clock
        self._cache: dict[str, WeatherReading] = {}  # last good live reading per zone

    def _simulated(self, zone: str, reason: str | None) -> WeatherReading | None:
        reading = self.simulation.get(zone)
        return reading.model_copy(update={"fallback_reason": reason}) if reading else None

    def get(self, zone: str, center: GeoPoint, force_refresh: bool = False) -> WeatherReading | None:
        """Current reading for a zone, or None only if no source at all has data for it."""
        if self.settings.weather_mode == "simulation":
            return self._simulated(zone, None)

        cached = self._cache.get(zone)
        max_age = timedelta(minutes=self.settings.weather_cache_min)
        if cached and not force_refresh and self.clock() - cached.fetched_at < max_age:
            return cached

        try:
            resp = self.http.get(self.settings.open_meteo_url, params={
                "latitude": center.lat, "longitude": center.lng,
                "current": "temperature_2m,precipitation,rain,wind_speed_10m,weather_code",
                "hourly": "precipitation,wind_speed_10m", "forecast_hours": 6,
            })
            resp.raise_for_status()
            reading = parse_open_meteo(zone, resp.json(), self.clock())
            self._cache[zone] = reading
            return reading
        except Exception as exc:  # noqa: BLE001 - network/parse errors degrade, never crash
            reason = f"Open-Meteo unavailable ({type(exc).__name__})"
            log.warning("%s for %s", reason, zone)
            if cached:
                return cached.model_copy(update={"stale": True, "fallback_reason": f"{reason}; last reading shown"})
            return self._simulated(zone, f"{reason}; simulation data shown")
