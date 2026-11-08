"""Make a cutscene event flow (an area intro) end at once while keeping what it leaves behind.

An area intro runs, mostly in one fork: a camera flyover, the player walking in (warp to a start, move, warp to the
final spot), Navi flying in, a fade in, the area name banner, sound ducking; then a camera reset and a Navi hint.
The instant version runs only: the player's final warp -> the area name banner -> the fade in -> the camera reset,
then ends. Those events already exist in the flow; only their next links and the entry point's first event change.
The event is still played through the normal path, so it is recorded as played (HavePlayedEvent).

usage: instant.py <event flow> [...]   prints the chain each entry point would get, changes nothing
"""
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from nxfmt import ROMFS, unzs
from evfl import Flow, END

ROLES = [   # (role, actions, keep every one or just one)
    ("flag", ["GameDataActor.EventTriggerSetGameDataBool"], True),
    ("time", ["EventWorldManagerController.EventTrigger_SetTime"], True),
    ("talked", ["EventTriggerSetHaveTalked"], True),
    ("final warp", ["Player_Event.EventPlayerWarpToTargetPosAndRot", "Player_Event.EventPlayerWarpToTargetActor",
                    "Player_Event.EventPlayerWarpToStarterLinkAnchor"], False),
    ("banner", ["UISystemActor.EventTriggerOpenLocationUI"], False),
    ("fade in", ["UISystemActor.EventEventFadeIn"], False),
    ("camera reset", ["EventCamera.EventTriggerSendReserveCameraResetMessageToPlayerCamera"], False),
]


SHOT = re.compile(r"C\d*(_\w+)?|Effects|SubFlow_\w+|Ready")   # entry points the flow calls itself


def outer_entries(f):
    """Entry points other flows start: not the shots (C01, C02_kokiri, ...) the flow calls itself."""
    return [ep for ep in f.entry_points() if not SHOT.fullmatch(ep)]


def reachable(f, start):
    """Events reachable from start, following calls into this flow's own entry points (its shots)."""
    eps = f.entry_points()
    order, seen, todo = [], set(), [start]
    while todo:
        i = todo.pop(0)
        if i == END or i in seen or i >= f.n_events:
            continue
        seen.add(i)
        order.append(i)
        e = f.event(i)
        if e["type"] == "subflow" and (not e["flowchart"] or e["flowchart"] == f.name) and e["entry"] in eps:
            todo.append(eps[e["entry"]][0])
        todo += [e["next"]] if "next" in e else []
        todo += [n for _, n, _ in e.get("cases", [])] + [n for n, _ in e.get("forks", [])]
        if e["type"] == "fork":
            todo.append(e["join"])
    return order


def chain_for(f, start):
    """[(role, event)] for one entry point, in run order."""
    acts = f.actors()

    def name(i):
        e = f.event(i)
        if e["type"] != "action":
            return e["type"]
        a = acts[e["actor"]]
        return f"{a['name']}.{a['actions'][e['action']]}"

    order = reachable(f, start)
    chain = []
    for role, actions, every in ROLES:
        hits = [i for i in order if any(name(i) == a or name(i).endswith("." + a) for a in actions)]
        if not hits:
            continue
        if every:
            chain += [(role, i) for i in hits]
            continue
        if role == "final warp":   # the warp after the walk-in, else the last one
            after_move = [i for i in hits if any(f.event(j).get("next") == i and name(j) == "Player_Event.EventPlayerMove"
                                                 for j in order)]
            hits = after_move or hits[-1:]
        chain.append((role, hits[-1] if role == "final warp" else hits[0]))
    return chain


def make_instant(data, entries=None):
    """Event flow data with each outer entry point (or those named) rerouted through its short chain."""
    f = Flow(data)
    out = bytearray(data)
    eps = f.entry_points()
    for ep in entries or outer_entries(f):
        first, pos = eps[ep]
        chain = chain_for(f, first)
        assert chain, f"{ep}: nothing to keep"
        for (_, a), (_, b) in zip(chain, chain[1:]):
            struct.pack_into("<H", out, f.event(a)["next_field"], b)
        struct.pack_into("<H", out, f.event(chain[-1][1])["next_field"], END)
        struct.pack_into("<H", out, pos, chain[0][1])
    return bytes(out)


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    for name in sys.argv[1:]:
        f = Flow(unzs((ROMFS / "Event" / "EventFlow" / f"{name}.bfevfl.zs").read_bytes()))
        for ep in outer_entries(f):
            first = f.entry_points()[ep][0]
            print(f"{name}::{ep} (starts at {first}): " + " -> ".join(f"{r} {i}" for r, i in chain_for(f, first)))
