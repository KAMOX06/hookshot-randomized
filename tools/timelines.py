"""Cutscene timelines: find the Dm_ events a flow plays, and which can be shortened safely.

A Dm_ event file that holds only a timeline (BFEVFL header: flowchart count u16 at +0x20, timeline count u16 at
+0x22, timeline pointer array at +0x38) has its length in frames as a float at +0x10 of the timeline (TLIN).
Shortening that to one frame ends the cutscene at once, while the game still plays it through its normal path, so
it is recorded as played (HavePlayedEvent) and every story check that asks about it still passes.
A timeline is only "safe" when its strings name no item, pouch, game data, challenge or warp action: those would be
cut off along with the pictures.

usage: timelines.py <event flow> [entry point]   lists the Dm_ events the flow (or that entry point) plays
"""
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from nxfmt import ROMFS, unzs

EVENTS = ROMFS / "Event" / "EventFlow"
STATEFUL = re.compile(r"Pouch|GameData|Challenge|SetHaveTalked|AddItem|GetItem|get_item|BancChange|"
                      r"(?<!Cloth)Warp(?!Mode)|Equip(?!ment(User|State)|mentSlot)", re.I)
HARMLESS = re.compile(r"Camera|DoF|PlayAS|EventWait|Wait$|Sound|Bgm|Fade|Participate|Offstage|Effect|LookAt|Turn|"
                      r"Shadow|Foliage|Graphics|ShadingParams|XLink|Rumble|Lod|Ducking|Locator|UI$|LocationUI|Talk|"
                      r"Message|Navi.*(Follow|Home)", re.I)


def flow_skip_info(name):
    """For a flowchart Dm_ event: (stateful actions, entry points, harmless actions that end the flow)."""
    from evfl import Flow, END
    p = EVENTS / f"{name}.bfevfl.zs"
    if not p.exists():
        return None
    d = unzs(p.read_bytes())
    if struct.unpack_from("<H", d, 0x20)[0] != 1:
        return None
    f = Flow(d)
    acts = f.actors()
    stateful, enders = [], []
    for e in f.events():
        if e["type"] != "action":
            continue
        a = acts[e["actor"]]
        act = a["actions"][e["action"]]
        if not HARMLESS.search(act):
            stateful.append(f"{a['name']}.{act}")
        elif e["next"] == END:
            enders.append((e["i"], f"{a['name']}.{act}"))
    return sorted(set(stateful)), f.entry_points(), enders


def timeline_info(name):
    """(duration, timeline offset, stateful strings) of a timeline-only event file, or None if it is not one."""
    p = EVENTS / f"{name}.bfevfl.zs"
    if not p.exists():
        return None
    d = unzs(p.read_bytes())
    n_fc, n_tl = struct.unpack_from("<HH", d, 0x20)
    if n_fc or n_tl != 1:
        return None
    t = struct.unpack_from("<Q", d, struct.unpack_from("<Q", d, 0x38)[0])[0]
    if d[t:t + 4] != b"TLIN":
        return None
    strs = {m.group().decode() for m in re.finditer(rb"[A-Za-z0-9_]{4,}", d)}
    return struct.unpack_from("<f", d, t + 0x10)[0], t, sorted(s for s in strs if STATEFUL.search(s))


def shorten(data, frames=1.0):
    """Timeline-only event data with its length set to frames."""
    n_fc, n_tl = struct.unpack_from("<HH", data, 0x20)
    assert n_fc == 0 and n_tl == 1, "not a timeline-only event file"
    t = struct.unpack_from("<Q", data, struct.unpack_from("<Q", data, 0x38)[0])[0]
    assert data[t:t + 4] == b"TLIN"
    out = bytearray(data)
    struct.pack_into("<f", out, t + 0x10, frames)
    return bytes(out)


def played_by(flow_name, entry=None):
    """Dm_ names the flow's EventTransition actions play (all of them, or those reachable from entry)."""
    from evfl import Flow, END
    f = Flow(unzs((EVENTS / f"{flow_name}.bfevfl.zs").read_bytes()))
    acts = f.actors()
    if entry:
        todo, seen = [f.entry_points()[entry][0]], set()
        while todo:
            i = todo.pop()
            if i == END or i in seen:
                continue
            seen.add(i)
            e = f.event(i)
            todo += [e["next"]] if "next" in e else []
            todo += [n for _, n, _ in e.get("cases", [])] + [n for n, _ in e.get("forks", [])]
            if e["type"] == "fork":
                todo.append(e["join"])
        events = [f.event(i) for i in sorted(seen)]
    else:
        events = f.events()
    out = []
    for e in events:
        if e["type"] == "action" and acts[e["actor"]]["actions"][e["action"]] == "EventTransition":
            s = f.params(e["params"]).get("EventName")
            if isinstance(s, str) and s.startswith("Dm_") and s not in out:
                out.append(s)
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    for name in played_by(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None):
        info = timeline_info(name)
        if info is None:
            if (ROMFS / "Event" / "Movie" / f"{name}.webm").exists():
                print(f"  {name:<44} movie")
                continue
            fi = flow_skip_info(name)
            if fi is None:
                print(f"  {name:<44} ?")
                continue
            stateful, entries, enders = fi
            print(f"  {name:<44} flow, {len(entries)} entry point(s), "
                  f"{'SAFE' if not stateful else 'state: ' + ', '.join(stateful[:5])}; harmless ends {[i for i, _ in enders][:4]}")
        else:
            dur, _, stateful = info
            print(f"  {name:<44} timeline {dur:7.0f} frames  {'SAFE' if not stateful else 'has ' + ', '.join(stateful[:4])}")
