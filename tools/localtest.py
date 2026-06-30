"""Local render harness — mocks Home Assistant state and renders scenarios.

Run from anywhere:

    python tools/localtest.py

It writes PNGs to `tools/out/` (gitignored). Each scenario produces:
  <name>_rgb.png       full-colour RGB render (what the renderer draws)
  <name>_epd_none.png  nearest-colour Spectra-6 quantization (what the panel shows)
  <name>_epd_fs.png    same quantization via the production dither_to_epd7() path

No Home Assistant install is required: a tiny FakeHass stands in for
`hass.states.get(...)`, so you can iterate on render.py offline. Only Pillow
is needed (`pip install pillow`).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COMPONENT = REPO / "custom_components" / "edashboard"
sys.path.insert(0, str(COMPONENT))

from backend_app.config import AppConfig  # noqa: E402
from backend_app.render import render_dashboard  # noqa: E402
from backend_app.epaper_format import dither_to_epd7, _build_palette_image  # noqa: E402


class FakeState:
    def __init__(self, value):
        self.state = str(value)


class FakeStates:
    def __init__(self, data):
        self.data = data

    def get(self, eid):
        v = self.data.get(eid)
        return FakeState(v) if v is not None else None


class FakeHass:
    def __init__(self, data):
        self.states = FakeStates(data)


W = "sensor.wittboy_"
P = "sensor.polleninformation_vicenza_"

# Representative live weather values.
WEATHER = {
    W + "outdoor_temperature": "25.3",
    W + "feels_like_temperature": "25.3",
    W + "humidity": "82",
    W + "dewpoint": "22.0",
    W + "wind_speed": "1.8",
    W + "wind_gust": "4.0",
    W + "wind_direction": "11",
    W + "absolute_pressure": "1012.0",
    W + "uv_index": "0",
    W + "solar_radiation": "3.0",
    W + "solar_lux": "379.7",
    W + "rain_rate_piezo": "0.0",
    W + "daily_rain_rate_piezo": "2.3",
}

ALL_NONE = {P + s: "none" for s in [
    "alder", "birch", "cypress_family", "fungal_spores", "grasses", "knotweed",
    "linden", "mugwort", "nettle_family", "olive", "plantain", "ragweed", "sweet_chestnut",
]}


def scenario(pollen_overrides, risk="high", weather_overrides=None):
    d = dict(WEATHER)
    d.update(ALL_NONE)
    d[P + "allergy_risk"] = risk
    d.update({P + k: v for k, v in pollen_overrides.items()})
    if weather_overrides:
        d.update({W + k: v for k, v in weather_overrides.items()})
    return FakeHass(d)


# "No data": weather present, but the pollen feed is entirely absent (every
# polleninformation sensor unavailable). The renderer must show NO DATA + an
# N/A risk badge, NOT the green ALL CLEAR state.
NODATA = FakeHass(dict(WEATHER))


SCENARIOS = {
    "real6": scenario({
        "nettle_family": "high", "plantain": "high", "sweet_chestnut": "high",
        "fungal_spores": "moderate", "grasses": "moderate", "cypress_family": "low",
    }, risk="high"),
    "calm2": scenario({"grasses": "moderate", "cypress_family": "low"}, risk="low"),
    "peak10": scenario({
        "nettle_family": "high", "plantain": "high", "sweet_chestnut": "high", "birch": "high",
        "fungal_spores": "moderate", "grasses": "moderate", "mugwort": "moderate",
        "cypress_family": "low", "olive": "low", "ragweed": "low",
    }, risk="very high"),
    "full13": scenario({
        "nettle_family": "very high", "plantain": "high", "sweet_chestnut": "high", "birch": "high",
        "fungal_spores": "moderate", "grasses": "moderate", "mugwort": "moderate", "knotweed": "moderate",
        "cypress_family": "low", "olive": "low", "ragweed": "low", "alder": "low", "linden": "low",
    }, risk="very high"),
    "none0": scenario({}, risk="none"),
    "nodata": NODATA,
}


def main():
    out = HERE / "out"
    out.mkdir(exist_ok=True)
    cfg = AppConfig(
        width=800, height=480,
        latitude=45.5, longitude=11.5,
        fonts_dir=COMPONENT / "assets" / "fonts",
        output_dir=out,
    )
    pal = _build_palette_image()
    for name, hass in SCENARIOS.items():
        img = render_dashboard(cfg, {}, {}, location_name="Vicenza", hass=hass)
        rgb = img.convert("RGB")
        rgb.save(out / f"{name}_rgb.png")
        rgb.quantize(palette=pal, dither=0).convert("RGB").save(out / f"{name}_epd_none.png")
        dither_to_epd7(img).convert("RGB").save(out / f"{name}_epd_fs.png")
        print("rendered", name)


if __name__ == "__main__":
    main()
