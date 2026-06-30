"""Init file for polleninformation.at integration (new API version).

Sets up the integration and coordinates data updates using only parameters supported by the new API.
All legacy parameters and imports have been removed.
"""

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

# Serve cached pollen data on a fetch failure (e.g. the API's 40 req/day limit
# or an HA restart) instead of going unavailable, up to this age. The upstream
# feed only updates a few times a day, so day-old values are still useful.
CACHE_MAX_AGE = timedelta(hours=36)
CACHE_VERSION = 1

from .api import (
    PollenApiAuthError,
    PollenApiConnectionError,
    PollenApiError,
    async_get_pollenat_data,
)
from .const import (
    CONF_APIKEY,
    CONF_COUNTRY,
    CONF_LANG,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_UPDATE_INTERVAL,
    DEFAULT_APIKEY,
    DEFAULT_COUNTRY,
    DEFAULT_LANG,
    DEFAULT_LATITUDE,
    DEFAULT_LONGITUDE,
    DEFAULT_UPDATE_INTERVAL,
    MAX_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .utils import get_country_code_map

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Initial setup of the integration using config entry."""

    # --- MIGRATION: convert country display names to ISO codes ---

    country_val = entry.data.get(CONF_COUNTRY)
    country_map = get_country_code_map(hass)
    if country_val and country_val not in country_map.values():
        code = country_map.get(country_val)
        if code:
            new_data = dict(entry.data)
            new_data[CONF_COUNTRY] = code
            hass.config_entries.async_update_entry(entry, data=new_data)
            _LOGGER.info(
                f"Migrated country display name '{country_val}' to code '{code}' for entry '{entry.title}'."
            )
    # ------------------------------------------------------------

    hass.data.setdefault(DOMAIN, {})

    # Fetch parameters: options override data (options flow writes to entry.options)
    def _opt(key, default=None):
        return entry.options.get(key, entry.data.get(key, default))

    lat = _opt(CONF_LATITUDE, DEFAULT_LATITUDE)
    lon = _opt(CONF_LONGITUDE, DEFAULT_LONGITUDE)
    country = _opt(CONF_COUNTRY, DEFAULT_COUNTRY)
    lang = _opt(CONF_LANG, DEFAULT_LANG)
    apikey = _opt(CONF_APIKEY, DEFAULT_APIKEY)

    # Clamp update_interval to valid range
    try:
        update_interval_hours = int(
            float(_opt(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))
        )
    except (TypeError, ValueError):
        update_interval_hours = DEFAULT_UPDATE_INTERVAL
    update_interval_hours = max(
        MIN_UPDATE_INTERVAL, min(MAX_UPDATE_INTERVAL, update_interval_hours)
    )
    scan_interval = timedelta(hours=update_interval_hours)

    _LOGGER.debug(
        "Setup entry with country=%s, lang=%s, interval=%sh",
        country,
        lang,
        update_interval_hours,
    )

    coordinator = PollenInformationDataUpdateCoordinator(
        hass, entry, lat, lon, country, lang, apikey, scan_interval
    )

    # First refresh to populate data
    try:
        await coordinator.async_config_entry_first_refresh()
    except UpdateFailed as err:
        _LOGGER.error("Error fetching initial data: %s", err)
        raise ConfigEntryNotReady

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Forward setup to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        domain_data = hass.data.get(DOMAIN)
        if domain_data is not None:
            domain_data.pop(entry.entry_id, None)
            if not domain_data:
                hass.data.pop(DOMAIN, None)
    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle config entry reload."""
    await hass.config_entries.async_reload(entry.entry_id)


class PollenInformationDataUpdateCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch data from polleninformation.at."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        lat,
        lon,
        country,
        lang,
        apikey,
        scan_interval,
    ):
        """Initialize the data coordinator with API parameters."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=scan_interval,
        )
        self.lat = lat
        self.lon = lon
        self.country = country
        self.lang = lang
        self.apikey = apikey
        self.last_updated = None
        self.serving_cache = False
        self._store: Store = Store(
            hass, CACHE_VERSION, f"{DOMAIN}_{config_entry.entry_id}_cache"
        )

    def _is_valid_api_response(self, result: dict | None) -> bool:
        if result is None:
            return False
        if not isinstance(result, dict):
            return False
        if "contamination" not in result:
            return False
        if not isinstance(result.get("contamination"), list):
            return False
        return True

    async def _async_save_cache(self, result: dict) -> None:
        """Persist the last good response so it survives restarts/rate-limits."""
        try:
            await self._store.async_save(
                {"ts": dt_util.utcnow().isoformat(), "data": result}
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not save polleninformation cache: %s", err)

    async def _async_load_cache(self) -> tuple[dict, object] | None:
        """Return (data, timestamp) from disk cache if present and fresh."""
        try:
            stored = await self._store.async_load()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not load polleninformation cache: %s", err)
            return None
        if not isinstance(stored, dict):
            return None
        data = stored.get("data")
        ts = dt_util.parse_datetime(stored.get("ts") or "")
        if ts is None or not self._is_valid_api_response(data):
            return None
        if dt_util.utcnow() - ts > CACHE_MAX_AGE:
            _LOGGER.debug("polleninformation cache is older than %s; ignoring", CACHE_MAX_AGE)
            return None
        return data, ts

    async def _async_fallback_to_cache(self, err: Exception) -> dict:
        """Serve last-good data on failure, or re-raise as UpdateFailed."""
        cached, ts = None, self.last_updated
        if self.data and self._is_valid_api_response(self.data):
            cached = self.data  # in-memory last good (this session)
        else:
            loaded = await self._async_load_cache()
            if loaded is not None:
                cached, ts = loaded
        if cached is not None:
            self.serving_cache = True
            _LOGGER.warning(
                "polleninformation fetch failed (%s); serving cached data from %s",
                err,
                ts,
            )
            return cached
        if isinstance(err, UpdateFailed):
            raise err
        raise UpdateFailed(str(err)) from err

    async def _async_update_data(self) -> dict:
        """Fetch latest pollen data from API, falling back to cached data."""
        _LOGGER.debug(
            "Fetching data for country=%s, lang=%s",
            self.country,
            self.lang,
        )
        try:
            result = await async_get_pollenat_data(
                self.hass,
                self.lat,
                self.lon,
                self.country,
                self.lang,
                self.apikey,
            )

            if not self._is_valid_api_response(result):
                raise UpdateFailed(
                    f"Invalid API response for {self.country}: missing or malformed data"
                )

            self.last_updated = dt_util.now()
            self.serving_cache = False
            await self._async_save_cache(result)
            return result  # type: ignore[return-value]
        except (UpdateFailed, PollenApiError) as err:
            return await self._async_fallback_to_cache(err)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Error fetching polleninformation.at: %s", err)
            return await self._async_fallback_to_cache(err)
