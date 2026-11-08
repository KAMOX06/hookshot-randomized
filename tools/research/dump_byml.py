"""Print BYML files (zstd or plain) as JSON, optionally only those containing a word.

usage: dump_byml.py <path relative to romfs, glob allowed> [word]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nxfmt import ROMFS, Byml, unzs

pattern = sys.argv[1]
word = sys.argv[2] if len(sys.argv) > 2 else None
for f in sorted(ROMFS.glob(pattern)):
    try:
        root = Byml(unzs(f.read_bytes())).root
    except Exception as ex:
        print(f"== {f.relative_to(ROMFS)}: not BYML ({ex})")
        continue
    text = json.dumps(root, indent=1, default=str)
    if word and word.lower() not in text.lower():
        continue
    print(f"== {f.relative_to(ROMFS)}")
    print(text[:4000])
