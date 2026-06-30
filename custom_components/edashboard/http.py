from __future__ import annotations

from pathlib import Path
import asyncio
import logging
import re
import time

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# On-demand refresh tuning. The device fetches this endpoint on every wake /
# manual refresh, so we re-render first to guarantee fresh data. A short min-age
# debounce avoids re-render storms, and a hard timeout bounds request latency.
_REFRESH_MIN_AGE_S = 10
_REFRESH_TIMEOUT_S = 12


def _sanitize_dashboard_name(raw_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", raw_name.strip()).strip("_").lower()


def _request_hass(request: web.Request) -> HomeAssistant:
    hass = request.app.get("hass")
    if hass is None:
        hass = request.config_dict.get("hass")
    if hass is None:
        raise web.HTTPServiceUnavailable(text="Home Assistant context is not available in this request")
    return hass


def _iter_location_candidates(raw_value: str) -> list[str]:
    base = _sanitize_dashboard_name(raw_value)
    out: list[str] = []
    for item in [raw_value.strip(), raw_value.strip().lower(), base, f"weather_{base}"]:
        name = _sanitize_dashboard_name(item)
        if name and name not in out:
            out.append(name)
    return out


def _discover_generated_paths(base_output: Path, names: list[str]) -> list[Path]:
    paths: list[Path] = []

    # Direct deterministic candidates first.
    for name in names:
        paths.append(base_output / name / "latest_epd.png")

    # Single-dashboard fallback layout.
    paths.append(base_output / "latest_epd.png")

    # Directory scan fallback for near matches.
    if base_output.exists() and base_output.is_dir():
        try:
            children = [p for p in base_output.iterdir() if p.is_dir()]
        except OSError:
            children = []

        for child in children:
            child_name = _sanitize_dashboard_name(child.name)
            if not child_name:
                continue
            if child_name in names:
                paths.append(child / "latest_epd.png")
                continue

            for name in names:
                if child_name.endswith(f"_{name}") or name in child_name:
                    paths.append(child / "latest_epd.png")
                    break

    # Deduplicate while preserving order.
    unique: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    return unique


_NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _match_runtime(runtimes: dict, dashboard_raw: str):
    """Resolve the requested dashboard to its runtime (single-dashboard friendly)."""
    if len(runtimes) == 1:
        return next(iter(runtimes.values()))
    key = _sanitize_dashboard_name(dashboard_raw)
    if key in runtimes:
        return runtimes[key]
    for name, rt in runtimes.items():
        if name and (name in key or key in name):
            return rt
    return None


async def _maybe_refresh_dashboard(
    hass: HomeAssistant, dashboard_raw: str, path_candidates: list[Path]
) -> None:
    """Best-effort on-demand re-render before serving; falls back to last image."""
    domain_data = hass.data.get(DOMAIN) or {}
    runtimes = domain_data.get("runtimes") or {}
    if not runtimes:
        return

    runtime = _match_runtime(runtimes, dashboard_raw)
    if runtime is None:
        return

    # Debounce: reuse a very recent render instead of re-rendering again.
    for candidate in path_candidates:
        try:
            if candidate.exists():
                if (time.time() - candidate.stat().st_mtime) < _REFRESH_MIN_AGE_S:
                    return
                break
        except OSError:
            continue

    try:
        await asyncio.wait_for(
            hass.async_add_executor_job(runtime.service.generate_once, hass, True),
            timeout=_REFRESH_TIMEOUT_S,
        )
        _LOGGER.debug("On-demand edashboard render complete for '%s'", dashboard_raw)
    except asyncio.TimeoutError:
        _LOGGER.warning("On-demand edashboard render timed out; serving last image")
    except Exception:  # noqa: BLE001
        _LOGGER.exception("On-demand edashboard render failed; serving last image")


class EDashboardLatestDashboardView(HomeAssistantView):
    url = "/api/edashboard/latest"
    name = "api:edashboard:latest_dashboard"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        try:
            hass = _request_hass(request)
            dashboard_raw = (request.query.get("dashboard") or "").strip()
            if not dashboard_raw:
                raise web.HTTPBadRequest(text="Query parameter 'dashboard' is required")

            base_output = Path(hass.config.path("www", "edashboard", "output")).resolve()
            unique_names = _iter_location_candidates(dashboard_raw)

            path_candidates = _discover_generated_paths(base_output, unique_names)

            # Re-render on demand so the device always pulls current data
            # (it fetches on wake / manual refresh). Opt out with ?fresh=0.
            if request.query.get("fresh", "1").strip().lower() not in ("0", "false", "no"):
                await _maybe_refresh_dashboard(hass, dashboard_raw, path_candidates)

            for candidate in path_candidates:
                if not candidate.exists() or not candidate.is_file():
                    continue
                try:
                    body = candidate.read_bytes()
                except OSError as exc:
                    _LOGGER.exception("Failed reading dashboard output file: %s", candidate)
                    continue
                return web.Response(body=body, content_type="image/png", headers=_NO_CACHE_HEADERS)

            raise web.HTTPNotFound(text=f"No generated dithered image found for dashboard: {dashboard_raw}")
        except web.HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("Unexpected error serving dashboard '%s'", dashboard_raw)
            raise web.HTTPNotFound(text=f"Dashboard API read error: {exc}") from exc


async def async_register_views(hass: HomeAssistant) -> None:
    hass.http.register_view(EDashboardLatestDashboardView())
