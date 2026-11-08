"""List actors that carry a given string anywhere in their data (any depth), in map files under an area.

usage: find_values.py <area folder or *> <substring>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nxfmt import ROMFS, load_byml


def strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for k, x in v.items():
            yield k
            yield from strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from strings(x)


area, needle = sys.argv[1:]
base = ROMFS / "Banc"
files = sorted(base.rglob("*.bcett.byml.zs")) if area == "*" else sorted((base / area).glob("*.bcett.byml.zs"))
for f in files:
    for a in load_byml(f).get("Actors", []) or []:
        if any(needle.lower() in s.lower() for s in strings(a)):
            shown = {k: v for k, v in a.items() if k not in ("Phive", "Rotate", "Scale")}
            print(f"{f.relative_to(base)}: {str(shown)[:700]}")
