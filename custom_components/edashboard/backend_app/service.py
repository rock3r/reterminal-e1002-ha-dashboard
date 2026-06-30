from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any
import json

from .config import AppConfig
from .data_sources import fetch_aqi, fetch_weather
from .epaper_format import dither_to_epd7, write_epd_binary
from .render import render_dashboard


class DashboardService:
    def __init__(self, config: AppConfig, location_name: str | None = None) -> None:
        self.config = config
        self.location_name = location_name
        self.lock = Lock()
        self.stop_event = Event()
        self.thread: Thread | None = None

        self.output_dir = Path(config.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @property
    def rgb_path(self) -> Path:
        return self.output_dir / "latest_rgb.png"

    @property
    def dithered_path(self) -> Path:
        return self.output_dir / "latest_epd.png"

    @property
    def binary_path(self) -> Path:
        return self.output_dir / "latest_epd.bin"

    @property
    def metadata_path(self) -> Path:
        return self.output_dir / "metadata.json"

    def generate_once(self, hass: Any | None = None, skip_remote_fetch: bool = False) -> dict[str, Any]:
        with self.lock:
            # The renderer draws purely from Home Assistant sensor states, so the
            # remote open-meteo fetch is optional. On-demand (device-facing)
            # renders skip it to keep the request fast and free of external deps.
            if skip_remote_fetch:
                weather, aqi = {}, {}
            else:
                weather = fetch_weather(self.config.latitude, self.config.longitude)
                aqi = fetch_aqi(self.config.latitude, self.config.longitude)

            rgb = render_dashboard(self.config, weather, aqi, location_name=self.location_name, hass=hass)
            rgb.save(self.rgb_path, format="PNG", optimize=True)

            indexed = dither_to_epd7(rgb)
            indexed.convert("RGB").save(self.dithered_path, format="PNG", optimize=True)
            payload_bytes = write_epd_binary(self.binary_path, self.config.width, self.config.height, indexed)

            metadata = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "width": self.config.width,
                "height": self.config.height,
                "palette_mode": "epd7",
                "binary_format": "EDB7-v1",
                "payload_bytes": payload_bytes,
                "binary_sha256": sha256(self.binary_path.read_bytes()).hexdigest(),
                "calendar_event": None,
                "config": {
                    "location": self.location_name,
                    "latitude": self.config.latitude,
                    "longitude": self.config.longitude,
                    "timezone": self.config.timezone,
                    "temp_unit": self.config.temp_unit,
                    "wind_unit": self.config.wind_unit,
                    "refresh_seconds": self.config.refresh_seconds,
                },
            }
            self.metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            return metadata

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return

        def loop() -> None:
            while not self.stop_event.is_set():
                try:
                    self.generate_once()
                except Exception as exc:
                    error_meta = {
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "error": str(exc),
                    }
                    self.metadata_path.write_text(json.dumps(error_meta, indent=2), encoding="utf-8")
                self.stop_event.wait(self.config.refresh_seconds)

        self.stop_event.clear()
        self.thread = Thread(target=loop, name="dashboard-generator", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
