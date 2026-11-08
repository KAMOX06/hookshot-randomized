"""Find files (decompressed, and inside SARC packs) under romfs folders that contain a string.

usage: find_text.py <string> <romfs subfolder> [<romfs subfolder> ...]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nxfmt import ROMFS, parse_sarc, unzs

needle, *folders = sys.argv[1:]
nb = needle.encode()
nw = needle.encode("utf-16-le")


def scan(label, blob):
    try:
        blob = unzs(blob)
    except Exception:
        return
    if blob[:4] == b"SARC":
        for inner, data in parse_sarc(blob).items():
            scan(f"{label}::{inner}", data)
    elif nb in blob or nw in blob:
        print(f"  {label}{' (utf-16)' if nw in blob and nb not in blob else ''}")


for folder in folders:
    for f in sorted((ROMFS / folder).rglob("*")):
        if f.is_file() and f.stat().st_size < 64 << 20:
            scan(str(f.relative_to(ROMFS)), f.read_bytes())
