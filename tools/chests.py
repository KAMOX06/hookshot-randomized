"""List every chest: where it is, what it holds, and whether the same chest (by Hash) shows up in several map files."""
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from nxfmt import ROMFS, load_byml

banc = ROMFS / "Banc"
chests = []
for f in sorted(banc.rglob("*.bcett.byml.zs")):
    root = load_byml(f)
    for a in (root or {}).get("Actors", []) or []:
        name = a.get("Gyaml", "")
        if "TBox" not in name or name.startswith("TBox_"):
            continue
        dyn = a.get("Dynamic") or {}
        chests.append({
            "file": str(f.relative_to(banc)),
            "actor": name,
            "hash": a.get("Hash"),
            "drop": dyn.get("Drop__DropActor"),
            "check": dyn.get("Drop__CheckActor"),
            "sub": dyn.get("Drop__DropActor_Substitute"),
            "event": dyn.get("GetEventName"),
            "repeat": dyn.get("IsEnableRepeatOpen"),
            "keys": sorted(k for k in dyn if k != "DefaultDungeonRoomIndex"),
        })

by_hash = collections.defaultdict(list)
for c in chests:
    by_hash[c["hash"]].append(c["file"])
print("chest entries:", len(chests), "| unique hashes:", len(by_hash))
print("hashes in more than one file:", sum(1 for v in by_hash.values() if len(set(v)) > 1))

contents = collections.Counter(c["drop"] or (f"(check) {c['check']}" if c["check"] else "(none)") for c in chests)
print("\ncontents:")
for k, n in contents.most_common():
    print(f"  {n:4}  {k}")
print("\nwith GetEventName:", sum(1 for c in chests if c["event"]))
print("progressive (CheckActor):", sum(1 for c in chests if c["check"]))
print("repeat-open (minigames):", sum(1 for c in chests if c["repeat"]))
print("\nchest actor kinds:", collections.Counter(c["actor"] for c in chests).most_common(15))
print("\nall Dynamic key combos:", collections.Counter(tuple(c["keys"]) for c in chests).most_common(12))
print("\nper area:", sorted(collections.Counter(c["file"].split("\\")[0] for c in chests).items()))
out = Path(__file__).parent / "chests.json"
out.write_text(json.dumps(chests, indent=1, default=str), encoding="utf-8")
print("\nwrote", out)
