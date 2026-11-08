"""List the events of an event flow whose params mention a string, with their action or query.

usage: events_using.py <flow> <string>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nxfmt import ROMFS, unzs
from evfl import Flow

name, needle = sys.argv[1:3]
f = Flow(unzs((ROMFS / "Event" / "EventFlow" / f"{name}.bfevfl.zs").read_bytes()))
acts = f.actors()
entries = f.entry_points()
for e in f.events():
    ps = f.param_strings(e.get("params"), span=0x120)
    if not any(needle in p for p in ps):
        continue
    a = acts[e["actor"]] if "actor" in e else None
    if e["type"] == "action":
        what = f"{a['name']}.{a['actions'][e['action']]}"
    elif e["type"] == "switch":
        what = f"if {a['name']}.{a['queries'][e['query']]} {[(v, n) for v, n, _ in e['cases']]}"
    else:
        what = e["type"]
    preds = [x["i"] for x in f.events() if x.get("next") == e["i"] or any(n == e["i"] for _, n, _ in x.get("cases", []))]
    print(f"{e['i']:4} {what}  next={e.get('next')}  from={preds}  params={ps[:8]}")
print("entry points:", {k: v[0] for k, v in entries.items()})
