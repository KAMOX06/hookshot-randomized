"""Show the formula of BoolExp game-data entries and what the hashes in it name (any GameData type).

usage: boolexp.py <BoolExp name> [...]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nxfmt import load_byml
from gamedata import GDL, murmur3, candidate_names

data = load_byml(GDL)["Data"]
names = candidate_names()
known = {murmur3(n.encode()): n for n in names}
where = {}
for kind, entries in data.items():
    for e in entries:
        if isinstance(e.get("Hash"), int):
            where[e["Hash"]] = (kind, e)
enum_values = {}
for e in data.get("Enum", []):
    for raw, v in zip(e.get("RawValues", []), e.get("Values", [])):
        enum_values[v] = f"{known.get(e['Hash'], hex(e['Hash']))}={raw}"

for name in sys.argv[1:]:
    h = murmur3(name.encode())
    kind, e = where[h]
    print(f"{name} ({kind}):")
    for op, val in e["Values"]:
        label = known.get(val) or enum_values.get(val) or (f"[{where[val][0]}]" if val in where else "")
        extra = ""
        if val in where:
            k2, e2 = where[val]
            extra = f" -> {k2} {known.get(val, hex(val))} default {e2.get('DefaultValue')}"
            if "RawValues" in e2:
                extra += f" values {e2['RawValues'][:12]}"
        print(f"   op {op:3}  value {val} ({hex(val)}) {label}{extra}")
