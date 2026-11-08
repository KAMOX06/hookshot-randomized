"""Search every GameDataList type (not just Bool) for entries whose recovered names contain a word.

Names are recovered by hashing candidate strings (event, map and logic strings, plus the challenge and step names
made up from them) with murmur3.

usage: gamedata_all.py <word> [<word> ...]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nxfmt import load_byml
from gamedata import GDL, murmur3, candidate_names

words = [w.lower() for w in sys.argv[1:]]
names = candidate_names()
extra = set()
for n in list(names):
    if "Challenge" in n or n.startswith("Step") or n in ("DoorOfTime", "Meet_DekuTree"):
        extra.add(n)
for base in ["DoorOfTime", "Meet_DekuTree", "Challenge"]:
    for suffix in ["", ".Step", ".CurrentStep", ".State", ".IsCompleted", ".IsStarted", "_Step", "_CurrentStep"]:
        extra.add(base + suffix)
known = {murmur3(n.encode()): n for n in names | extra}
data = load_byml(GDL)["Data"]
for kind, entries in data.items():
    for e in entries:
        h = e.get("Hash")
        n = known.get(h) if isinstance(h, int) else None
        if n and any(w in n.lower() for w in words):
            shown = {k: v for k, v in e.items() if k not in ("Hash", "RawValues", "Values")}
            print(f"{kind:<10} {n}: {shown}")
