from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
import asyncio
import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .const import (
    CONF_HEADER_TITLE,
    CONF_LOCATION,
    CONF_OUTPUT_DIR,
    CONF_POLLEN_PREFIX,
    CONF_REFRESH_SECONDS,
    CONF_TEMP_UNIT,
    CONF_WEATHER_PREFIX,
    CONF_WIND_UNIT,
    DEFAULT_REFRESH_SECONDS,
    DOMAIN,
)
from .http import async_register_views
from .backend_app.config import AppConfig
from .backend_app.data_sources import geocode_location
from .backend_app.service import DashboardService

_LOGGER = logging.getLogger(__name__)

_DASHBOARD_OPTION_KEYS = {
    CONF_OUTPUT_DIR,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_LOCATION,
    CONF_TEMP_UNIT,
    CONF_WIND_UNIT,
    CONF_REFRESH_SECONDS,
    CONF_WEATHER_PREFIX,
    CONF_POLLEN_PREFIX,
    CONF_HEADER_TITLE,
}

CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional(DOMAIN): vol.Any(
            dict,
            [dict],
        )
    },
    extra=vol.ALLOW_EXTRA,
)


@dataclass
class DashboardRuntime:
    service: Any
    output_dir: Path
    refresh_seconds: int
    generate_lock: Any
    unsub_interval: Any | None = None
    last_error: str | None = None
    last_success: str | None = None


def _sanitize_dashboard_name(raw_name: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", raw_name.strip()).strip("_").lower()
    return cleaned or fallback


def _normalize_entries(raw_cfg: Any) -> list[tuple[str, dict[str, Any]]]:
    if raw_cfg is None:
        return [("default", {})]

    if isinstance(raw_cfg, list):
        entries: list[tuple[str, dict[str, Any]]] = []
        used_names: set[str] = set()
        for index, item in enumerate(raw_cfg, start=1):
            if not isinstance(item, dict):
                raise ValueError("Each edashboard list item must be a mapping")

            explicit_name: str | None = None
            explicit_cfg: dict[str, Any] | None = None
            item_keys = list(item.keys())

            if len(item_keys) == 1:
                only_key = str(item_keys[0])
                only_val = item[item_keys[0]]
                if isinstance(only_val, dict):
                    explicit_name = only_key
                    explicit_cfg = dict(only_val)

            if explicit_cfg is None:
                name_candidates = [str(k) for k in item.keys() if str(k) not in _DASHBOARD_OPTION_KEYS]
                if name_candidates:
                    explicit_name = name_candidates[0]
                explicit_cfg = dict(item)
                if explicit_name is not None:
                    explicit_cfg.pop(explicit_name, None)

            base_name = explicit_name or f"dashboard_{index}"
            name = _sanitize_dashboard_name(base_name, f"dashboard_{index}")
            if name in used_names:
                suffix = 2
                unique_name = f"{name}_{suffix}"
                while unique_name in used_names:
                    suffix += 1
                    unique_name = f"{name}_{suffix}"
                name = unique_name

            used_names.add(name)
            entries.append((name, explicit_cfg))

        return entries

    if isinstance(raw_cfg, dict):
        if any(k in raw_cfg for k in _DASHBOARD_OPTION_KEYS):
            return [("default", dict(raw_cfg))]

        entries = []
        for index, (name_raw, cfg_raw) in enumerate(raw_cfg.items(), start=1):
            if not isinstance(cfg_raw, dict):
                raise ValueError(f"Dashboard '{name_raw}' must map to a dictionary")
            name = _sanitize_dashboard_name(str(name_raw), f"dashboard_{index}")
            entries.append((name, dict(cfg_raw)))
        return entries or [("default", {})]

    raise ValueError("edashboard configuration must be a mapping or a list of mappings")


async def _resolve_location(hass: HomeAssistant, cfg: dict[str, Any]) -> tuple[float, float, str, str]:
    location = str(cfg.get(CONF_LOCATION, "")).strip()
    if location:
        geo = await hass.async_add_executor_job(geocode_location, location)
        lat = float(geo["latitude"])
        lon = float(geo["longitude"])
        timezone = str(geo["timezone"])
        location_name = str(geo.get("name") or location)
        country = str(geo.get("country") or "").strip()
        if country:
            location_name = f"{location_name}, {country}"
        return lat, lon, timezone, location_name

    lat = float(cfg.get(CONF_LATITUDE, hass.config.latitude))
    lon = float(cfg.get(CONF_LONGITUDE, hass.config.longitude))
    timezone = str(hass.config.time_zone or "UTC")
    location_name = str(hass.config.location_name or "Home").strip() or "Home"
    return lat, lon, timezone, location_name

def _build_runtime_config(
    hass: HomeAssistant,
    cfg: dict[str, Any],
    dashboard_name: str,
    multi_mode: bool,
    lat: float,
    lon: float,
    timezone: str,
    location_name: str,
) -> DashboardRuntime:

    # Home Assistant unit APIs changed across versions. Keep defaults stable and
    # infer metric from configured temperature unit when available.
    ha_temp_unit = str(getattr(hass.config.units, "temperature_unit", "")).upper()
    inferred_metric = "C" in ha_temp_unit

    temp_unit = str(cfg.get(CONF_TEMP_UNIT, "C" if inferred_metric else "F")).upper()
    wind_unit = str(cfg.get(CONF_WIND_UNIT, "km/h" if inferred_metric else "mph"))

    refresh_seconds = int(cfg.get(CONF_REFRESH_SECONDS, DEFAULT_REFRESH_SECONDS))

    if CONF_OUTPUT_DIR in cfg:
        output_dir = Path(str(cfg[CONF_OUTPUT_DIR])).expanduser().resolve()
    else:
        base_output = Path(hass.config.path("www", "edashboard", "output")).expanduser().resolve()
        output_dir = base_output / dashboard_name if multi_mode else base_output

    output_dir.mkdir(parents=True, exist_ok=True)

    # Entity prefixes / header title. Left empty unless explicitly configured;
    # the renderer derives sensible defaults from the location otherwise.
    weather_prefix = str(cfg.get(CONF_WEATHER_PREFIX, "")).strip() or "sensor.wittboy_"
    pollen_prefix = str(cfg.get(CONF_POLLEN_PREFIX, "")).strip()
    header_title = str(cfg.get(CONF_HEADER_TITLE, "")).strip()

    app_cfg = AppConfig(
        width=800,
        height=480,
        refresh_seconds=max(15, refresh_seconds),
        timezone=timezone,
        latitude=lat,
        longitude=lon,
        temp_unit=temp_unit,
        wind_unit=wind_unit,
        output_dir=output_dir,
        secrets_path=None,
        backend_config_path=Path(__file__).resolve().parent / "config.yaml",
        fonts_dir=Path(__file__).resolve().parent / "assets" / "fonts",
        weather_prefix=weather_prefix,
        pollen_prefix=pollen_prefix,
        header_title=header_title,
    )

    service = DashboardService(app_cfg, location_name)

    return DashboardRuntime(
        service=service,
        output_dir=output_dir,
        refresh_seconds=max(15, refresh_seconds),
        generate_lock=None,
    )


async def _generate_once(hass: HomeAssistant, runtime: DashboardRuntime, reason: str) -> dict[str, Any] | None:
    async with runtime.generate_lock:
        _LOGGER.debug("Generating eDashboard payload (%s)", reason)
        try:
            metadata = await hass.async_add_executor_job(runtime.service.generate_once, hass)
        except Exception as exc:  # noqa: BLE001
            runtime.last_error = str(exc)
            _LOGGER.exception("eDashboard generation failed (%s)", reason)
            return None

        runtime.last_error = None
        runtime.last_success = str(metadata.get("generated_at", ""))
        return metadata


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    raw_cfg = config.get(DOMAIN, {})

    try:
        entries = _normalize_entries(raw_cfg)
        multi_mode = len(entries) > 1

        runtimes: dict[str, DashboardRuntime] = {}
        for dashboard_name, cfg in entries:
            lat, lon, timezone, location_name = await _resolve_location(hass, cfg)
            runtime = _build_runtime_config(
                hass,
                cfg,
                dashboard_name,
                multi_mode,
                lat,
                lon,
                timezone,
                location_name,
            )
            runtime.generate_lock = asyncio.Lock()
            runtimes[dashboard_name] = runtime
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Failed to initialize eDashboard integration: %s", exc)
        return False

    default_dashboard = next(iter(runtimes.keys()))
    hass.data[DOMAIN] = {
        "runtimes": runtimes,
        "default_dashboard": default_dashboard,
    }

    await async_register_views(hass)

    async def _handle_generate(call: ServiceCall) -> None:
        target = str(call.data.get("dashboard", "")).strip().lower()
        if target:
            runtime = runtimes.get(target)
            if runtime is None:
                _LOGGER.warning("Unknown dashboard '%s' requested in generate_now service", target)
                return
            await _generate_once(hass, runtime, f"service:{target}")
            return

        for name, runtime in runtimes.items():
            await _generate_once(hass, runtime, f"service:{name}")

    if not hass.services.has_service(DOMAIN, "generate_now"):
        hass.services.async_register(DOMAIN, "generate_now", _handle_generate)

    for name, runtime in runtimes.items():
        await _generate_once(hass, runtime, f"startup:{name}")

        # Polled integrations (weather, pollen) may still be populating their
        # sensors a few seconds after startup, so the boot render can miss data.
        # Schedule a couple of one-shot "warm-up" re-renders so the dashboard
        # self-corrects within ~75s of any restart instead of waiting a full cycle.
        for warmup_delay in (75, 240):
            async def _warmup(_now, rt: DashboardRuntime = runtime, dn: str = name) -> None:
                await _generate_once(hass, rt, f"warmup:{dn}")

            async_call_later(hass, warmup_delay, _warmup)

        async def _scheduled(_now, dashboard_name: str = name, rt: DashboardRuntime = runtime) -> None:
            await _generate_once(hass, rt, f"scheduled:{dashboard_name}")

        runtime.unsub_interval = async_track_time_interval(
            hass,
            _scheduled,
            timedelta(seconds=runtime.refresh_seconds),
        )

    async def _on_stop(_event) -> None:
        for runtime in runtimes.values():
            if runtime.unsub_interval:
                runtime.unsub_interval()
        if hass.services.has_service(DOMAIN, "generate_now"):
            hass.services.async_remove(DOMAIN, "generate_now")
        for runtime in runtimes.values():
            await hass.async_add_executor_job(runtime.service.stop)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _on_stop)

    _LOGGER.info("eDashboard initialized with %s dashboard(s)", len(runtimes))
    for name, runtime in runtimes.items():
        _LOGGER.info(
            "eDashboard dashboard '%s': output_dir=%s refresh=%ss",
            name,
            runtime.output_dir,
            runtime.refresh_seconds,
        )

    return True
