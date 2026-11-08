"""For Dm_ cutscenes: which event flows play them (EventTransition with that EventName, and from which entry point),
their kind, and the state-changing actions in them.

usage: who_plays.py <Dm name> [...]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nxfmt import ROMFS, unzs
from evfl import Flow, END
import timelines

sys.stdout.reconfigure(errors="replace")
EVENTS = ROMFS / "Event" / "EventFlow"
wanted = set(sys.argv[1:])
callers = {w: [] for w in wanted}
for p in sorted(EVENTS.glob("*.bfevfl.zs")):
    raw = unzs(p.read_bytes())
    if not any(w.encode() in raw for w in wanted):
        continue
    try:
        f = Flow(raw)
    except AssertionError:
        continue
    acts = f.actors()
    entry_of = {}
    for ep, (first, _) in f.entry_points().items():
        todo, seen = [first], set()
        while todo:
            i = todo.pop()
            if i == END or i in seen or i >= f.n_events:
                continue
            seen.add(i)
            entry_of.setdefault(i, ep)
            e = f.event(i)
            todo += [e["next"]] if "next" in e else []
            todo += [n for _, n, _ in e.get("cases", [])] + [n for n, _ in e.get("forks", [])]
            if e["type"] == "fork":
                todo.append(e["join"])
    for e in f.events():
        if e["type"] == "action" and acts[e["actor"]]["actions"][e["action"]] == "EventTransition":
            name = f.params(e["params"]).get("EventName")
            if name in wanted:
                callers[name].append(f"{p.name[:-11]}::{entry_of.get(e['i'], '?')} (event {e['i']})")
for w in sys.argv[1:]:
    info = timelines.timeline_info(w)
    if info:
        kind = f"timeline {info[0]:.0f} frames, state {info[2] or 'none'}"
    elif (ROMFS / "Event" / "Movie" / f"{w}.webm").exists():
        kind = "movie"
    else:
        fi = timelines.flow_skip_info(w)
        kind = "flow, state: " + (", ".join(fi[0]) or "none") if fi else "?"
    print(f"{w}: {kind}")
    print(f"    played by: {callers[w] or 'nothing found'}")
