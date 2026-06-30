"""Rebuild the bundled fonts in custom_components/edashboard/assets/fonts/.

You only need to run this if you want to change the icon set or font weights;
the pre-built fonts are already committed. Requires `fonttools`
(`pip install fonttools`) and the source variable fonts placed next to this
script (they are gitignored because they are large and freely downloadable):

  - Archivo.ttf        Archivo variable font (OFL) — https://fonts.google.com/specimen/Archivo
  - MSR.ttf            Material Symbols Rounded variable font (Apache-2.0)
                       https://github.com/google/material-design-icons
  - MSR.codepoints     the matching "name codepoint" list shipped beside MSR.ttf

Run from anywhere:

    python tools/fonts_src/build_fonts.py
"""
from fontTools import ttLib, subset
from fontTools.varLib.instancer import instantiateVariableFont
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = REPO / "custom_components" / "edashboard" / "assets" / "fonts"
OUT.mkdir(parents=True, exist_ok=True)

# --- Archivo static weights ---
for wght, name in [(900, "Archivo-Black"), (800, "Archivo-ExtraBold"), (700, "Archivo-Bold")]:
    f = ttLib.TTFont(str(HERE / "Archivo.ttf"))
    instantiateVariableFont(f, {"wght": wght, "wdth": 100}, inplace=True)
    # rename so PIL reports a sensible family
    f.save(str(OUT / f"{name}.ttf"))
    print("wrote", name, (OUT / f"{name}.ttf").stat().st_size)

# --- Material Symbols Rounded: filled, then subset to needed glyphs ---
ms = ttLib.TTFont(str(HERE / "MSR.ttf"))
instantiateVariableFont(ms, {"FILL": 1, "GRAD": 0, "opsz": 48, "wght": 500}, inplace=True)
ms.save(str(HERE / "MSR-filled.ttf"))

needed = ["eco", "potted_plant", "grass", "forest", "cloud", "park", "local_florist", "psychiatry", "sync"]
cps = {}
for line in (HERE / "MSR.codepoints").read_text().splitlines():
    n, c = line.split()
    cps[n] = int(c, 16)
unicodes = [cps[n] for n in needed]
print("subset unicodes:", [(n, hex(cps[n])) for n in needed])

ss = subset.Subsetter()
ssfont = ttLib.TTFont(str(HERE / "MSR-filled.ttf"))
ss.populate(unicodes=unicodes)
ss.subset(ssfont)
ssfont.save(str(OUT / "MaterialSymbolsRounded-Filled.ttf"))
print("wrote MaterialSymbolsRounded-Filled.ttf", (OUT / "MaterialSymbolsRounded-Filled.ttf").stat().st_size)
