"""Print every event an entry point of an event flow reaches, in order of discovery, with its params' strings.

usage: walk_entry.py <flow> <entry point> [params span in bytes, default 0x60]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nxfmt import ROMFS, unzs
from evfl import Flow, END

sys.stdout.reconfigure(errors="replace")   # some params hold Japanese text
name, entry = sys.argv[1:3]
span = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x60
f = Flow(unzs((ROMFS / "Event" / "EventFlow" / f"{name}.bfevfl.zs").read_bytes()))
acts = f.actors()
start = f.entry_points()[entry][0]
seen, todo = set(), [start]
while todo:
    i = todo.pop(0)
    if i == END or i in seen:
        continue
    seen.add(i)
    e = f.event(i)
    desc = e["type"]
    if e["type"] == "action":
        a = acts[e["actor"]]
        desc = f"{a['name']}.{a['actions'][e['action']]}"
    elif e["type"] == "switch":
        a = acts[e["actor"]]
        desc = f"if {a['name']}.{a['queries'][e['query']]} {[(v, n) for v, n, _ in e['cases']]}"
    elif e["type"] == "subflow":
        desc = f"call {e['flowchart'] or name}::{e['entry']}"
    elif e["type"] == "fork":
        desc = f"fork {[n for n, _ in e['forks']]} join {e['join']}"
    print(f"  {i:3} -> {e.get('next', '')!s:5} {desc}  {f.param_strings(e.get('params'), span)[:8]}")
    todo += [e["next"]] if "next" in e else []
    todo += [n for _, n, _ in e.get("cases", [])] + [n for n, _ in e.get("forks", [])]
    if e["type"] == "fork":
        todo.append(e["join"])
