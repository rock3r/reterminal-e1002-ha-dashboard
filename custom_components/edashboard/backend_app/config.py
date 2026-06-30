from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
from typing import Any

import yaml


@dataclass
class AppConfig:
    width: int = 800
    height: int = 480
    refresh_seconds: int = 60
    timezone: str = "UTC"
    latitude: float = 0.0
    longitude: float = 0.0
    temp_unit: str = "C"
    wind_unit: str = "km/h"
    output_dir: Path = Path("backend/output")
    secrets_path: Path | None = None
    backend_config_path: Path = Path("backend/config.yaml")
    fonts_dir: Path = Path("backend/assets/fonts")
    # Entity-id prefixes and on-screen title. Empty pollen_prefix/header_title
    # are derived from the location at render time (see render.py).
    weather_prefix: str = "sensor.wittboy_"
    pollen_prefix: str = ""
    header_title: str = ""


class ConfigError(RuntimeError):
    pass


def _read_yaml_optional(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Backend config file must contain a top-level YAML object: {path}")
    return data


def load_config() -> AppConfig:
    repo_root = Path(os.getenv("APP_ROOT", ".")).resolve()
    secrets_path_env = os.getenv("SECRETS_PATH", "").strip()
    secrets_path = Path(secrets_path_env).resolve() if secrets_path_env else None
    backend_config_path = Path(os.getenv("BACKEND_CONFIG_PATH", repo_root / "backend/config.yaml")).resolve()
    output_dir = Path(os.getenv("OUTPUT_DIR", repo_root / "backend/output")).resolve()
    fonts_dir = Path(os.getenv("FONTS_DIR", repo_root / "backend/assets/fonts")).resolve()

    payload = _read_yaml_optional(secrets_path) if secrets_path else {}
    backend_payload = _read_yaml_optional(backend_config_path)

    latitude = float(os.getenv("LATITUDE", backend_payload.get("latitude", payload.get("latitude", 0.0))))
    longitude = float(os.getenv("LONGITUDE", backend_payload.get("longitude", payload.get("longitude", 0.0))))
    if latitude == 0.0 and longitude == 0.0:
        raise ConfigError("latitude and longitude are required (backend/config.yaml or LATITUDE/LONGITUDE env)")

    timezone = str(os.getenv("TIMEZONE", backend_payload.get("timezone", "UTC"))).strip() or "UTC"
    temp_unit = str(os.getenv("TEMP_UNIT", backend_payload.get("temp_unit", "C"))).strip().upper()
    wind_unit = str(os.getenv("WIND_UNIT", backend_payload.get("wind_unit", "km/h"))).strip()
    refresh_seconds = int(os.getenv("REFRESH_SECONDS", str(backend_payload.get("refresh_seconds", 60))))

    return AppConfig(
        timezone=timezone,
        latitude=latitude,
        longitude=longitude,
        temp_unit=temp_unit,
        wind_unit=wind_unit,
        refresh_seconds=max(15, refresh_seconds),
        output_dir=output_dir,
        secrets_path=secrets_path,
        backend_config_path=backend_config_path,
        fonts_dir=fonts_dir,
    )
