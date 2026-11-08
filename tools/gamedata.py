"""GameDataList: the game's flags, named by murmur3 32-bit hashes of their names (as in TotK).

usage: gamedata.py [word ...]   lists Bool flags whose names, recovered from event and map strings, contain a word
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from nxfmt import ROMFS, load_byml, unzs

GDL = next((ROMFS / "GameData").glob("GameDataList.Product.*.byml.zs"))


def murmur3(data, seed=0):
    c1, c2, h = 0xCC9E2D51, 0x1B873593, seed
    n = len(data) & ~3
    for i in range(0, n, 4):
        k = int.from_bytes(data[i:i + 4], "little")
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        k = (k * c2) & 0xFFFFFFFF
        h ^= k
        h = ((h << 13) | (h >> 19)) & 0xFFFFFFFF
        h = (h * 5 + 0xE6546B64) & 0xFFFFFFFF
    k = 0
    tail = data[n:]
    if len(tail) >= 3: k ^= tail[2] << 16
    if len(tail) >= 2: k ^= tail[1] << 8
    if len(tail) >= 1:
        k ^= tail[0]
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        k = (k * c2) & 0xFFFFFFFF
        h ^= k
    h ^= len(data)
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & 0xFFFFFFFF
    h ^= h >> 16
    return h


def candidate_names():
    """Strings from event flows, maps and logic modules that may be flag names."""
    names = set()
    files = list((ROMFS / "Event" / "EventFlow").glob("*.bfevfl.zs")) + list((ROMFS / "Banc").rglob("*.bcett.byml.zs"))
    files += list((ROMFS / "Logic").rglob("*.ainb"))
    for f in files:
        for m in re.finditer(rb"[A-Za-z0-9_.\-]{4,}", unzs(f.read_bytes())):
            names.add(m.group().decode())
    return names


if __name__ == "__main__":
    data = load_byml(GDL)["Data"]
    words = [w.lower() for w in sys.argv[1:]]
    bools = {e["Hash"]: e for e in data["Bool"]}
    known = {murmur3(n.encode()): n for n in candidate_names()}
    print(f"{GDL.name}: {len(bools)} Bool flags, {sum(1 for h in bools if h in known)} named")
    for h, e in sorted(bools.items(), key=lambda kv: known.get(kv[0], "")):
        if h in known and any(w in known[h].lower() for w in words):
            print(f"  {known[h]}: default {e['DefaultValue']}  reset {e['ResetTypeValue']}")
