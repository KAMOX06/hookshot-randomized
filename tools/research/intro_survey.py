"""For every Dm_ cutscene a flow plays: its kind, whether game data has a HavePlayedEvent flag for it, and the first
event of each of its entry points.

usage: intro_survey.py <event flow> [entry point]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nxfmt import ROMFS, unzs, load_byml
from evfl import Flow
from gamedata import GDL, murmur3
import timelines

sys.stdout.reconfigure(errors="replace")
bools = {e["Hash"] for e in load_byml(GDL)["Data"]["Bool"]}
for name in timelines.played_by(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None):
    p = ROMFS / "Event" / "EventFlow" / f"{name}.bfevfl.zs"
    flag = murmur3(f"HavePlayedEvent.{name}".encode()) in bools
    if not p.exists():
        kind = "movie" if (ROMFS / "Event" / "Movie" / f"{name}.webm").exists() else "no file"
        print(f"{name:<36} {kind:<9} flag={flag}")
        continue
    try:
        f = Flow(unzs(p.read_bytes()))
    except AssertionError:
        print(f"{name:<36} {'timeline':<9} flag={flag}")
        continue
    acts = f.actors()
    firsts = []
    for ep, (i, _) in f.entry_points().items():
        e = f.event(i)
        what = f"{acts[e['actor']]['name']}.{acts[e['actor']]['actions'][e['action']]}" if e["type"] == "action" else e["type"]
        firsts.append(f"{ep}:{i} {what} next={e.get('next')}")
    print(f"{name:<36} {'flow':<9} flag={flag}  " + " | ".join(firsts))
