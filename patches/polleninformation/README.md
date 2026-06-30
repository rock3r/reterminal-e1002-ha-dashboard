# `polleninformation` caching patch

This is **not** a full integration. It is a single drop-in replacement for the
`__init__.py` of the third-party **`polleninformation`** Home Assistant
integration (the Austrian Pollen Information Service / *polleninformation.at*).

- Upstream integration: **`krissen/polleninformation`** —
  <https://github.com/krissen/polleninformation> (creates sensors from
  polleninformation.eu / the Austrian Pollen Information Service public API,
  which requires a personal API key). Install it first via HACS or manually;
  this patch only replaces one file.

## What it adds

The upstream coordinator goes **unavailable** whenever a fetch fails — and the
public API is easy to trip up: it enforces a **~40 requests/day limit**, and a
Home Assistant restart triggers another fetch. When the feed only refreshes a
few times a day, going unavailable means the dashboard loses its pollen data for
hours over nothing.

This patched `DataUpdateCoordinator`:

1. **Persists the last good response to disk** (HA `Store`), so it survives
   restarts — not just the in-memory session.
2. **Serves the last-good data on any fetch failure** (rate-limit, network
   error, malformed response) instead of going unavailable, up to a max age
   (`CACHE_MAX_AGE`, default **36 hours** — the feed only updates a few times a
   day, so day-old values are still useful).
3. Validates responses (`contamination` present and well-formed) before caching
   or serving them, and exposes a `serving_cache` flag for visibility.

The net effect: the reTerminal dashboard keeps showing the most recent real
pollen data through rate-limit windows and restarts, and only shows **NO DATA**
if there has genuinely never been a good fetch within the cache window.

## How to apply

1. Install the upstream `polleninformation` integration (HACS or manual) and
   configure it for your location.
2. Replace its `__init__.py` with the one in this folder:
   ```
   custom_components/polleninformation/__init__.py  ←  patches/polleninformation/__init__.py
   ```
3. Restart Home Assistant.

> Re-applying after an upstream update: this patch only changes `__init__.py`.
> If a future upstream release changes the coordinator's constructor or the
> response shape, re-check `_is_valid_api_response()` and the coordinator
> `__init__` signature against the new upstream before copying it over.

This file is a modification of upstream and remains under upstream's license.
