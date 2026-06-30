from __future__ import annotations

from pathlib import Path
import struct

from PIL import Image


EPD7_PALETTE = [
    (255, 255, 255),  # 0 white
    (0, 0, 0),        # 1 black
    (0, 160, 70),     # 2 green
    (0, 95, 200),     # 3 blue
    (220, 0, 0),      # 4 red
    (245, 205, 0),    # 5 yellow
    (0, 255, 255),    # 6 cyan (orange is not native on Spectra E6)
]


def _build_palette_image() -> Image.Image:
    palette_img = Image.new("P", (1, 1))
    palette_bytes: list[int] = []
    for color in EPD7_PALETTE:
        palette_bytes.extend(color)
    while len(palette_bytes) < 256 * 3:
        palette_bytes.extend((0, 0, 0))
    palette_img.putpalette(palette_bytes[: 256 * 3])
    return palette_img


def dither_to_epd7(image: Image.Image) -> Image.Image:
    # The dashboard is composed entirely of flat Spectra-6 palette colours, so a
    # nearest-colour map (no diffusion) keeps text edges and solid badges crisp
    # on e-ink. Floyd-Steinberg would only add speckle/bleed on the flat fills.
    src = image.convert("RGB")
    palette_img = _build_palette_image()
    return src.quantize(palette=palette_img, dither=Image.Dither.NONE)


def pack_4bpp(indices: bytes) -> bytes:
    out = bytearray((len(indices) + 1) // 2)
    for i in range(0, len(indices), 2):
        hi = indices[i] & 0x0F
        lo = indices[i + 1] & 0x0F if i + 1 < len(indices) else 0
        out[i // 2] = (hi << 4) | lo
    return bytes(out)


def write_epd_binary(path: Path, width: int, height: int, indexed_img: Image.Image) -> int:
    if indexed_img.mode != "P":
        raise ValueError("Expected indexed palette image (mode P)")

    indices = bytes(indexed_img.getdata())
    payload = pack_4bpp(indices)

    header = struct.pack(
        "<4sBHHBBHI",
        b"EDB7",  # Magic
        1,         # Version
        width,
        height,
        1,         # Format: 4bpp packed indices
        len(EPD7_PALETTE),
        0,         # Reserved
        len(payload),
    )

    path.write_bytes(header + payload)
    return len(payload)
