from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
import re
from PIL import Image, ImageDraw, ImageFont

from .config import AppConfig


def _slug(value: str) -> str:
    """Lowercase, collapse non-alphanumerics to underscores (for entity ids)."""
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")

ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"

# ---------------------------------------------------------------------------
# Spectra 6 native palette (1:1 with the e-paper hardware colours so flat
# fills never dither). Only these colours are used for fills.
# ---------------------------------------------------------------------------
WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)
GREEN = (0, 160, 70, 255)
BLUE = (0, 95, 200, 255)
RED = (220, 0, 0, 255)
YELLOW = (245, 205, 0, 255)
GREY = (235, 235, 235, 255)

# Canvas geometry (mirrors the Claude design export, 800x480).
PAD = 14
HEADER_BASELINE = 42
HEADER_RULE_Y = 58
CONTENT_RIGHT = 786

# Left weather column
CARD_BOX = (14, 66, 393, 210)
CARD_DIVIDER_X = 264
TILE_W, TILE_H = 120, 118
TILE_ROW1_Y = 221
TILE_ROW2_Y = 348
TILE_COLS_X = (14, 143, 273)

# Right allergen column
RC_LEFT = 407
RC_RIGHT = 786
LIST_TOP = 132
LIST_BOTTOM = 466
LIST_GAP = 8

# Material Symbols Rounded glyph code points (subset bundled in assets/fonts).
ICON_GLYPHS = {
    "eco": chr(0xEA35),
    "potted_plant": chr(0xF8AA),
    "grass": chr(0xF205),
    "forest": chr(0xEA99),
    "cloud": chr(0xF15C),
    "park": chr(0xEA63),
    "local_florist": chr(0xE545),
    "psychiatry": chr(0xE123),
    "sync": chr(0xE627),
}

# Pollen metadata: sensor slug -> (display name, icon name)
POLLENS = [
    ("nettle_family", "Nettle family", "eco"),
    ("plantain", "Plantain", "potted_plant"),
    ("grasses", "Grasses", "grass"),
    ("sweet_chestnut", "Sweet chestnut", "forest"),
    ("fungal_spores", "Fungal spores", "cloud"),
    ("cypress_family", "Cypress family", "park"),
    ("olive", "Olive", "eco"),
    ("birch", "Birch", "forest"),
    ("alder", "Alder", "park"),
    ("mugwort", "Mugwort", "local_florist"),
    ("ragweed", "Ragweed", "local_florist"),
    ("knotweed", "Knotweed", "psychiatry"),
    ("linden", "Linden", "forest"),
]

# level string -> (severity score, short label)
_LEVEL_INFO = {
    "very high": (4, "V.HIGH"),
    "high": (3, "HIGH"),
    "moderate": (2, "MID"),
    "low": (1, "LOW"),
    "none": (0, "NONE"),
}


def _level_info(raw: str) -> tuple[int, str]:
    return _LEVEL_INFO.get(str(raw).strip().lower(), (0, "NONE"))


# Green-card rendering on the physical panel. The Spectra-6 green primary
# renders as a pale yellow-green; an ordered dither can deepen it (mix BLACK)
# or cool it toward a truer green (mix BLUE). "solid" keeps a flat green.
#   "solid" -> flat green, black text   |   "black"/"blue" -> dithered, white text
GREEN_DITHER = "solid"
GREEN_DITHER_RATIO = 0.34  # fraction of the mix colour woven into the green


def _green_fg() -> tuple:
    return BLACK if GREEN_DITHER == "solid" else WHITE


def _level_style(score: int) -> tuple[tuple, tuple]:
    """(background, foreground) for a severity score.

    Foregrounds are tuned for the physical Spectra-6 panel: its green primary
    renders pale, so white text washes out on flat green (hence dithering it
    darker + white text, or black text on flat green). Red keeps white text.
    """
    if score >= 3:
        return RED, WHITE
    if score == 2:
        return YELLOW, BLACK
    if score == 1:
        return GREEN, _green_fg()
    return GREY, BLACK


_BAYER4 = [
    [0, 8, 2, 10],
    [12, 4, 14, 6],
    [3, 11, 1, 9],
    [15, 7, 13, 5],
]


def _dither_tile(color_a: tuple, color_b: tuple, ratio: float) -> Image.Image:
    """4x4 ordered-dither tile mixing color_b into color_a at the given ratio."""
    tile = Image.new("RGBA", (4, 4))
    px = tile.load()
    for y in range(4):
        for x in range(4):
            px[x, y] = color_b if (_BAYER4[y][x] + 0.5) / 16.0 < ratio else color_a
    return tile


def _fill_dither(img, box, radius, color_a, color_b, ratio):
    """Fill a rounded region with an ordered dither of color_a/color_b."""
    x0, y0, x1, y1 = (int(v) for v in box)
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    tile = _dither_tile(color_a, color_b, ratio)
    pattern = Image.new("RGBA", (w, h))
    for ty in range(0, h, 4):
        for tx in range(0, w, 4):
            pattern.paste(tile, (tx, ty))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    img.paste(pattern, (x0, y0), mask)


def _fill_card(img, draw, box, radius, score):
    """Fill an allergen card: dithered for green (when enabled), else solid."""
    bg, _ = _level_style(score)
    if score == 1 and GREEN_DITHER != "solid":
        mix = BLUE if GREEN_DITHER == "blue" else BLACK
        _fill_dither(img, box, radius, GREEN, mix, GREEN_DITHER_RATIO)
    else:
        draw.rounded_rectangle(box, radius=radius, fill=bg)


# ---------------------------------------------------------------------------
# Font loading (static Archivo weights + Material Symbols, with fallbacks)
# ---------------------------------------------------------------------------
def _load(fonts_dir: Path, name: str, size: int) -> ImageFont.FreeTypeFont:
    for base in (fonts_dir, FONTS_DIR):
        p = base / name
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size=size)
            except Exception:
                pass
    for fb in ("Jost-SemiBold.ttf", "Jost.ttf"):
        p = FONTS_DIR / fb
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size=size)
            except Exception:
                pass
    return ImageFont.load_default()


class Fonts:
    def __init__(self, fonts_dir: Path) -> None:
        self.black = lambda s: _load(fonts_dir, "Archivo-Black.ttf", s)
        self.xbold = lambda s: _load(fonts_dir, "Archivo-ExtraBold.ttf", s)
        self.bold = lambda s: _load(fonts_dir, "Archivo-Bold.ttf", s)
        self.icon = lambda s: _load(fonts_dir, "MaterialSymbolsRounded-Filled.ttf", s)
        self.icons_available = any(
            (base / "MaterialSymbolsRounded-Filled.ttf").exists()
            for base in (fonts_dir, FONTS_DIR)
        )


def _top_bar(draw, box, radius, fill):
    """Filled bar with only the top corners rounded (square bottom)."""
    try:
        draw.rounded_rectangle(box, radius=radius, fill=fill,
                               corners=(True, True, False, False))
    except TypeError:  # very old Pillow without the corners kwarg
        draw.rectangle(box, fill=fill)


def _text(draw, xy, s, font, fill, anchor="lm"):
    draw.text(xy, s, font=font, fill=fill, anchor=anchor)


def _tw(draw, s, font) -> int:
    return int(draw.textlength(s, font=font))


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------
def render_dashboard(
    cfg: AppConfig,
    weather: dict[str, Any],
    aqi: dict[str, Any],
    location_name: str | None = None,
    hass: Any | None = None,
) -> Image.Image:
    img = Image.new("RGBA", (cfg.width, cfg.height), WHITE)
    draw = ImageDraw.Draw(img)
    F = Fonts(cfg.fonts_dir)

    def avail(entity_id: str) -> str | None:
        """Actual sensor state, or None if missing/unavailable/unknown."""
        if hass is None:
            return None
        st = hass.states.get(entity_id)
        if st is None or st.state in ("unknown", "unavailable", "", None):
            return None
        return st.state

    def state(entity_id: str, default: str = "--") -> str:
        v = avail(entity_id)
        return default if v is None else v

    def fnum(entity_id: str, default: float) -> float:
        try:
            return float(state(entity_id, str(default)))
        except (TypeError, ValueError):
            return default

    # Entity prefixes and the header title are configurable. When unset they are
    # derived from the location, so nothing is hardcoded to one city/station.
    city = (location_name or "").split(",")[0].strip()
    wp = (getattr(cfg, "weather_prefix", "") or "sensor.wittboy_").strip()
    pp = (getattr(cfg, "pollen_prefix", "") or "").strip()
    if not pp:
        pp = f"sensor.polleninformation_{_slug(city) or 'home'}_"
    title = (getattr(cfg, "header_title", "") or "").strip() or (city.upper() if city else "WEATHER")

    # --- Weather values (Ecowitt / Wittboy by default) ----------------------
    temp = fnum(f"{wp}outdoor_temperature", 25.3)
    feels = fnum(f"{wp}feels_like_temperature", 25.3)
    humidity = fnum(f"{wp}humidity", 82)
    dewpoint = fnum(f"{wp}dewpoint", 22.0)
    wind = fnum(f"{wp}wind_speed", 1.8)
    gust = fnum(f"{wp}wind_gust", 4.0)
    wind_dir = fnum(f"{wp}wind_direction", 11)
    pressure = fnum(f"{wp}absolute_pressure", 1012.0)
    uv = fnum(f"{wp}uv_index", 0)
    solar = fnum(f"{wp}solar_radiation", 3.0)
    lux = fnum(f"{wp}solar_lux", 379.7)
    rain_rate = fnum(f"{wp}rain_rate_piezo", 0.0)
    rain_today = fnum(f"{wp}daily_rain_rate_piezo", 2.3)

    # --- Header --------------------------------------------------------------
    _draw_header(draw, F, title)

    # --- Left column ---------------------------------------------------------
    _draw_weather_card(draw, F, temp, feels, humidity, dewpoint)

    tiles = [
        dict(label="WIND", value=f"{wind:.1f}", vsize=25, unit="km/h",
             unit_pos="inline", sub=f"GUST {gust:.1f}"),
        dict(label="PRESSURE", value=f"{pressure:.1f}", vsize=22, unit="hPa",
             unit_pos="below", sub=None),
        dict(label="UV INDEX", value=f"{uv:.0f}", vsize=25, unit=None,
             unit_pos=None, sub=_uv_category(uv)),
        dict(label="SOLAR", value=f"{solar:.0f}", vsize=25, unit="W/m²",
             unit_pos="inline", sub=f"{lux:,.1f} LX"),
        dict(label="RAIN", value=f"{rain_rate:.1f}", vsize=25, unit="mm/h",
             unit_pos="inline", sub=f"TODAY {rain_today:.1f}"),
        dict(label="WIND DIR", value=f"{wind_dir:.0f}", vsize=25, unit="°",
             unit_pos="inline", sub=_compass(wind_dir)),
    ]
    positions = [
        (TILE_COLS_X[0], TILE_ROW1_Y), (TILE_COLS_X[1], TILE_ROW1_Y), (TILE_COLS_X[2], TILE_ROW1_Y),
        (TILE_COLS_X[0], TILE_ROW2_Y), (TILE_COLS_X[1], TILE_ROW2_Y), (TILE_COLS_X[2], TILE_ROW2_Y),
    ]
    for spec, (x0, y0) in zip(tiles, positions):
        _draw_tile(draw, F, x0, y0, spec)

    # --- Right column --------------------------------------------------------
    # Distinguish "unavailable" (no data) from "none" (genuinely all-clear).
    risk_raw = avail(f"{pp}allergy_risk")
    _draw_allergen_header(draw, F, risk_raw)

    active = []
    have_data = False
    for slug, name, icon in POLLENS:
        raw = avail(f"{pp}{slug}")
        if raw is None:
            continue
        have_data = True
        score, label = _level_info(raw)
        if score >= 1:
            active.append((score, name, icon, label))
    active.sort(key=lambda t: (-t[0], t[1].lower()))

    _draw_allergen_list(img, draw, F, active, have_data)

    return img


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
def _draw_header(draw, F, title="WEATHER"):
    title = (title or "WEATHER").strip() or "WEATHER"
    _text(draw, (PAD, HEADER_BASELINE), title, F.black(26), BLACK, anchor="ls")
    vw = _tw(draw, title, F.black(26))
    _text(draw, (PAD + vw + 14, HEADER_BASELINE), "WEATHER & POLLEN",
          F.xbold(13), BLACK, anchor="ls")

    now = datetime.now()
    days = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
    months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    date_str = f"{days[now.weekday()]} {now.day:02d} {months[now.month - 1]} {now.year}"
    refreshed = f"REFRESHED {now:%H:%M} · {now.day:02d} {months[now.month - 1]}"

    right = 636
    _text(draw, (right, 27), date_str, F.xbold(13), BLACK, anchor="rs")

    rfont = F.bold(13)
    rw = _tw(draw, refreshed, rfont)
    _text(draw, (right, 43), refreshed, rfont, BLACK, anchor="rs")
    if F.icons_available:
        _text(draw, (right - rw - 8, 39), ICON_GLYPHS["sync"], F.icon(15), BLACK, anchor="rm")

    draw.rectangle((PAD, HEADER_RULE_Y, CONTENT_RIGHT, HEADER_RULE_Y + 2), fill=BLACK)


# ---------------------------------------------------------------------------
# Weather card (big temperature + humidity / dewpoint)
# ---------------------------------------------------------------------------
def _draw_weather_card(draw, F, temp, feels, humidity, dewpoint):
    x0, y0, x1, y1 = CARD_BOX
    draw.rounded_rectangle((x0, y0, x1, y1), radius=22, outline=BLACK, width=3)
    draw.line((CARD_DIVIDER_X, y0 + 18, CARD_DIVIDER_X, y1 - 18), fill=BLACK, width=3)

    lx = x0 + 21
    _text(draw, (lx, 96), "TEMPERATURE", F.xbold(13), BLACK, anchor="ls")

    # Auto-fit the big temperature so it never crosses the vertical divider,
    # whatever the value's width (e.g. "100.2°", "-12.4°").
    temp_str = f"{temp:.1f}°"
    avail = (CARD_DIVIDER_X - 2) - (lx - 2) - 6
    tsize = 86
    tfont = F.black(tsize)
    while _tw(draw, temp_str, tfont) > avail and tsize > 48:
        tsize -= 2
        tfont = F.black(tsize)
    _text(draw, (lx - 2, 170), temp_str, tfont, BLACK, anchor="ls")

    _text(draw, (lx, 192), f"FEELS LIKE {feels:.1f}°C", F.xbold(14), BLACK, anchor="ls")

    rx = CARD_DIVIDER_X + 15
    _text(draw, (rx, 95), "HUMIDITY", F.xbold(13), BLACK, anchor="ls")
    _text(draw, (rx, 128), f"{humidity:.0f}%", F.black(34), BLACK, anchor="ls")
    _text(draw, (rx, 158), "DEWPOINT", F.xbold(13), BLACK, anchor="ls")
    _text(draw, (rx, 190), f"{dewpoint:.1f}°", F.black(34), BLACK, anchor="ls")


# ---------------------------------------------------------------------------
# Metric tile
# ---------------------------------------------------------------------------
def _draw_tile(draw, F, x0, y0, spec):
    x1, y1 = x0 + TILE_W, y0 + TILE_H
    draw.rounded_rectangle((x0, y0, x1, y1), radius=16, outline=BLACK, width=3)
    _top_bar(draw, (x0 + 3, y0 + 3, x1 - 3, y0 + 26), radius=13, fill=BLACK)
    _text(draw, (x0 + 13, y0 + 15), spec["label"], F.xbold(12), WHITE, anchor="lm")

    vx = x0 + 13
    base = y0 + 76
    vfont = F.black(spec["vsize"])
    _text(draw, (vx, base), spec["value"], vfont, BLACK, anchor="ls")
    vw = _tw(draw, spec["value"], vfont)

    if spec["unit_pos"] == "inline" and spec["unit"]:
        gap = 1 if spec["unit"] == "°" else 4
        _text(draw, (vx + vw + gap, base), spec["unit"], F.xbold(12), BLACK, anchor="ls")
    elif spec["unit_pos"] == "below" and spec["unit"]:
        _text(draw, (vx, y0 + 97), spec["unit"], F.xbold(13), BLACK, anchor="ls")

    if spec["sub"]:
        _text(draw, (vx, y0 + 97), spec["sub"], F.xbold(13), BLACK, anchor="ls")


# ---------------------------------------------------------------------------
# Allergen header + RISK badge
# ---------------------------------------------------------------------------
def _draw_allergen_header(draw, F, risk_raw):
    _text(draw, (RC_LEFT, 87), "POLLEN & ALLERGY", F.xbold(13), BLACK, anchor="ls")
    _text(draw, (RC_LEFT, 112), "ACTIVE ALLERGENS", F.black(23), BLACK, anchor="ls")

    if risk_raw is None:
        # No data: neutral outlined badge, not the red "HIGH" fallback.
        label, fg, fill, outline = "N/A", BLACK, None, BLACK
    else:
        score, label = _level_info(risk_raw)
        fill, fg = _level_style(score)
        outline = None

    # Keep wide labels (e.g. "V.HIGH") from crowding the title.
    lfont = F.black(26 if len(label) <= 4 else 20)
    lw = _tw(draw, label, lfont)
    badge_w = max(lw + 36, 80)
    bx0 = RC_RIGHT - badge_w
    if fill is not None:
        draw.rounded_rectangle((bx0, 66, RC_RIGHT, 121), radius=16, fill=fill)
    else:
        draw.rounded_rectangle((bx0, 66, RC_RIGHT, 121), radius=16, outline=outline, width=3)
    _text(draw, (RC_RIGHT - 18, 86), "RISK", F.xbold(12), fg, anchor="rs")
    _text(draw, (RC_RIGHT - 18, 113), label, lfont, fg, anchor="rs")


# ---------------------------------------------------------------------------
# Allergen list (responsive: single column <=7, two columns otherwise)
# ---------------------------------------------------------------------------
def _draw_allergen_list(img, draw, F, active, have_data=True):
    n = len(active)
    if n == 0:
        # Subtle light-grey (dithered) panel with a thin border and dark text.
        # "no data" and "all clear" are deliberately different messages.
        box = (RC_LEFT, LIST_TOP, RC_RIGHT, LIST_BOTTOM)
        _fill_dither(img, box, 16, WHITE, BLACK, 0.12)
        draw.rounded_rectangle(box, radius=16, outline=BLACK, width=2)
        cx = (RC_LEFT + RC_RIGHT) // 2
        cy = (LIST_TOP + LIST_BOTTOM) // 2
        if have_data:
            if F.icons_available:
                _text(draw, (cx, cy - 48), ICON_GLYPHS["eco"], F.icon(44), BLACK, anchor="mm")
            _text(draw, (cx, cy + 8), "ALL CLEAR", F.black(34), BLACK, anchor="mm")
            _text(draw, (cx, cy + 40), "no active allergens", F.xbold(16), BLACK, anchor="mm")
        else:
            if F.icons_available:
                _text(draw, (cx, cy - 48), ICON_GLYPHS["sync"], F.icon(40), BLACK, anchor="mm")
            _text(draw, (cx, cy + 6), "NO DATA", F.black(34), BLACK, anchor="mm")
            _text(draw, (cx, cy + 38), "pollen feed unavailable", F.xbold(16), BLACK, anchor="mm")
        return

    total_h = LIST_BOTTOM - LIST_TOP
    if n <= 7:
        _draw_list_single(img, draw, F, active, n, total_h)
    else:
        _draw_list_grid(img, draw, F, active, n, total_h)


def _draw_list_single(img, draw, F, active, n, total_h):
    bar_h = (total_h - (n - 1) * LIST_GAP) / n
    name_size = 21 if bar_h >= 44 else 18
    icon_size = 30 if bar_h >= 44 else 24
    for i, (score, name, icon, label) in enumerate(active):
        y0 = LIST_TOP + i * (bar_h + LIST_GAP)
        y1 = y0 + bar_h
        bg, fg = _level_style(score)
        _fill_card(img, draw, (RC_LEFT, y0, RC_RIGHT, y1), 16, score)
        cy = (y0 + y1) / 2

        if F.icons_available and icon in ICON_GLYPHS:
            _text(draw, (RC_LEFT + 33, cy + 1), ICON_GLYPHS[icon], F.icon(icon_size), fg, anchor="mm")
        _text(draw, (RC_LEFT + 62, cy), name, F.black(name_size), fg, anchor="lm")

        pfont = F.black(14)
        pw = _tw(draw, label, pfont) + 24
        px1 = RC_RIGHT - 18
        px0 = px1 - pw
        ph = 26
        draw.rounded_rectangle((px0, cy - ph / 2, px1, cy + ph / 2), radius=8,
                               outline=fg, width=2)
        _text(draw, ((px0 + px1) / 2, cy), label, pfont, fg, anchor="mm")


def _draw_list_grid(img, draw, F, active, n, total_h):
    rows = (n + 1) // 2
    gap = LIST_GAP
    cell_h = (total_h - (rows - 1) * gap) / rows
    col_w = (RC_RIGHT - RC_LEFT - gap) / 2
    name_size = 18 if cell_h >= 50 else 15
    icon_size = 26 if cell_h >= 50 else 21
    for i, (score, name, icon, label) in enumerate(active):
        r, c = divmod(i, 2)
        x0 = RC_LEFT + c * (col_w + gap)
        x1 = x0 + col_w
        y0 = LIST_TOP + r * (cell_h + gap)
        y1 = y0 + cell_h
        bg, fg = _level_style(score)
        _fill_card(img, draw, (x0, y0, x1, y1), 14, score)
        cy = (y0 + y1) / 2
        tx = x0 + 14
        if F.icons_available and icon in ICON_GLYPHS:
            _text(draw, (x0 + 26, cy + 1), ICON_GLYPHS[icon], F.icon(icon_size), fg, anchor="mm")
            tx = x0 + 48
        _draw_fit(draw, (tx, cy), name, F, name_size, fg, max_w=int(x1 - tx - 12))


def _draw_fit(draw, xy, text, F, size, fill, max_w):
    s = size
    font = F.black(s)
    while _tw(draw, text, font) > max_w and s > 11:
        s -= 1
        font = F.black(s)
    _text(draw, xy, text, font, fill, anchor="lm")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _compass(deg: float) -> str:
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    try:
        return dirs[int((float(deg) + 11.25) / 22.5) % 16]
    except (TypeError, ValueError):
        return "--"


def _uv_category(uv: float) -> str:
    if uv >= 11:
        return "EXTREME"
    if uv >= 8:
        return "V.HIGH"
    if uv >= 6:
        return "HIGH"
    if uv >= 3:
        return "MODERATE"
    return "LOW"
