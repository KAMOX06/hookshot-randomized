"""Read event flow files (BFEVFL version 4, the layout of this game) far enough to list a flowchart's events and
rewire them in place. Worked out from OpeningEvent; close to Breath of the Wild's documented version 3, but:

Flowchart header (EVFL): counts at +0x10 (actors, actions, queries, events u16) and +0x18 (entry points);
  pointers at +0x20 name, +0x28 actors, +0x30 events, +0x38 entry point DIC, +0x40 entry points.
Actor (0x38): name, secondary name, argument name, actions (array of string pointers), queries, params,
  then action count u16, query count u16.
Event (0x30): name pointer, then by type (u8 at +8):
  0 action:  next u16 +0xA, actor u16 +0xC, action u16 +0xE, params ptr +0x10
  1 switch:  case count +0xA, actor +0xC, query +0xE, params +0x10, cases +0x18 (case, 8 bytes: u32 value, u16 event)
  2 fork:    fork count +0xA, join +0xC, forks ptr +0x10 (u16 each)
  3 join:    next +0xA
  4 subflow: next +0xA, params +0x10, flowchart name +0x18, entry point name +0x20
Strings are pointers to a u16 length and the text. 0xFFFF means "no next event".

usage: evfl.py <event flow name>   prints the entry points and events, with the strings in each event's params
"""
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from nxfmt import ROMFS, unzs

TYPES = {0: "action", 1: "switch", 2: "fork", 3: "join", 4: "subflow"}
END = 0xFFFF
EVENT_SIZE = 0x30
ACTOR_SIZE = 0x38


class Flow:
    def __init__(self, data):
        self.d = data
        assert data[:6] == b"BFEVFL", data[:6]
        self.fc = struct.unpack_from("<Q", data, struct.unpack_from("<Q", data, 0x28)[0])[0]   # first flowchart
        f = self.fc
        assert data[f:f + 4] == b"EVFL"
        self.n_actors, self.n_actions, self.n_queries, self.n_events = struct.unpack_from("<4H", data, f + 0x10)
        self.n_entries = struct.unpack_from("<H", data, f + 0x18)[0]
        self.name = self.s(struct.unpack_from("<Q", data, f + 0x20)[0])
        self.actors_ptr, self.events_ptr, self.entry_dict_ptr, self.entries_ptr = struct.unpack_from("<4Q", data, f + 0x28)
        assert data[self.entry_dict_ptr:self.entry_dict_ptr + 4] == b"DIC "

    def s(self, ptr):
        if not ptr or ptr + 2 > len(self.d):
            return None
        n = struct.unpack_from("<H", self.d, ptr)[0]
        return self.d[ptr + 2:ptr + 2 + n].decode("utf-8", "replace")

    def actors(self):
        out = []
        for i in range(self.n_actors):
            o = self.actors_ptr + ACTOR_SIZE * i
            name, sub, arg, actions, queries = struct.unpack_from("<5Q", self.d, o)
            n_act, n_q = struct.unpack_from("<HH", self.d, o + 0x30)
            acts = [self.s(struct.unpack_from("<Q", self.d, actions + 8 * k)[0]) for k in range(n_act)] if actions else []
            qs = [self.s(struct.unpack_from("<Q", self.d, queries + 8 * k)[0]) for k in range(n_q)] if queries else []
            out.append({"name": self.s(name), "sub": self.s(sub), "actions": acts, "queries": qs})
        return out

    def event(self, i):
        o = self.events_ptr + EVENT_SIZE * i
        t = self.d[o + 8]
        e = {"i": i, "off": o, "name": self.s(struct.unpack_from("<Q", self.d, o)[0]), "type": TYPES.get(t, t)}
        if t in (0, 3, 4):
            e["next"] = struct.unpack_from("<H", self.d, o + 0xA)[0]
            e["next_field"] = o + 0xA
        if t == 0:
            e["actor"], e["action"] = struct.unpack_from("<HH", self.d, o + 0xC)
            e["params"] = struct.unpack_from("<Q", self.d, o + 0x10)[0]
        if t == 4:
            e["params"] = struct.unpack_from("<Q", self.d, o + 0x10)[0]
            e["flowchart"] = self.s(struct.unpack_from("<Q", self.d, o + 0x18)[0])
            e["entry"] = self.s(struct.unpack_from("<Q", self.d, o + 0x20)[0])
        if t == 1:
            n, e["actor"], e["query"] = struct.unpack_from("<HHH", self.d, o + 0xA)
            e["params"] = struct.unpack_from("<Q", self.d, o + 0x10)[0]
            cases = struct.unpack_from("<Q", self.d, o + 0x18)[0]
            e["cases"] = [(struct.unpack_from("<I", self.d, cases + 8 * k)[0],
                           struct.unpack_from("<H", self.d, cases + 8 * k + 4)[0], cases + 8 * k + 4) for k in range(n)]
        if t == 2:
            n, e["join"] = struct.unpack_from("<HH", self.d, o + 0xA)
            forks = struct.unpack_from("<Q", self.d, o + 0x10)[0]
            e["forks"] = [(struct.unpack_from("<H", self.d, forks + 2 * k)[0], forks + 2 * k) for k in range(n)]
        return e

    def events(self):
        return [self.event(i) for i in range(self.n_events)]

    def param_strings(self, ptr, span=0x200):
        """Strings pointed at from the params container at ptr (a rough look: pointers in the next span bytes)."""
        if not ptr:
            return []
        out = []
        for p in range(ptr, min(ptr + span, len(self.d) - 8), 8):
            v = struct.unpack_from("<Q", self.d, p)[0]
            if 0 < v < len(self.d) - 2:
                s = self.s(v)
                if s and re.fullmatch(r"[\w./:\-]{3,}", s) and s not in out:
                    out.append(s)
        return out

    def params(self, ptr):
        """{key: value} of an event's params container: type u8, -, count u16, -, DIC pointer at +8, then one value
        pointer per key at +0x10; a value has its type u8 at +0 and its data at +0x10 (a string pointer for strings,
        the number itself otherwise)."""
        if not ptr:
            return {}
        n = struct.unpack_from("<H", self.d, ptr + 2)[0]
        dic = struct.unpack_from("<Q", self.d, ptr + 8)[0]
        out = {}
        for k in range(n):
            key = self.s(struct.unpack_from("<Q", self.d, dic + 8 + 0x10 * (k + 1) + 8)[0])
            v = struct.unpack_from("<Q", self.d, ptr + 0x10 + 8 * k)[0]
            raw = struct.unpack_from("<Q", self.d, v + 0x10)[0]
            val = raw & 0xFFFFFFFF
            if 0 < raw < len(self.d) - 2:
                s = self.s(raw)
                if s and s.isprintable() and len(s) < 200:
                    val = s
            out[key] = val
        return out

    def entry_names(self):
        d = self.entry_dict_ptr
        n = struct.unpack_from("<I", self.d, d + 4)[0]
        return [self.s(struct.unpack_from("<Q", self.d, d + 8 + 0x10 * k + 8)[0]) for k in range(1, n + 1)]

    def entry_points(self):
        """{name: (first event, field position)}. EntryPoint (0x20): sub flow indices, variable defs and their DIC
        pointers, then sub flow count u16, variable count u16, first event u16 at +0x1C."""
        out = {}
        for k, nm in enumerate(self.entry_names()):
            pos = self.entries_ptr + 0x20 * k + 0x1C
            out[nm] = (struct.unpack_from("<H", self.d, pos)[0], pos)
        return out


def links(flow):
    """(from event, to event, field position) for every next / case / fork link."""
    out = []
    for e in flow.events():
        if "next" in e and e["next"] != END:
            out.append((e["i"], e["next"], e["next_field"]))
        for v, nxt, pos in e.get("cases", []):
            out.append((e["i"], nxt, pos))
        for nxt, pos in e.get("forks", []):
            out.append((e["i"], nxt, pos))
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # (some flows hold Japanese names)
    f = Flow(unzs((ROMFS / "Event" / "EventFlow" / f"{sys.argv[1]}.bfevfl.zs").read_bytes()))
    acts = f.actors()
    targets = {t for _, t, _ in links(f)}
    print(f"flowchart {f.name}: {f.n_events} events; entry points {f.entry_points()}")
    print("events nothing leads to (entry points):", [e["i"] for e in f.events() if e["i"] not in targets])
    for e in f.events():
        extra = ""
        if e["type"] == "action":
            a = acts[e["actor"]]
            extra = f"{a['name']}.{a['actions'][e['action']] if e['action'] < len(a['actions']) else e['action']}"
        if e["type"] == "subflow":
            extra = f"-> {e['flowchart'] or f.name}::{e['entry']}"
        if e["type"] == "switch":
            a = acts[e["actor"]]
            q = a["queries"][e["query"]] if e["query"] < len(a["queries"]) else e["query"]
            extra = f"{a['name']}.{q} cases {[(v, n) for v, n, _ in e['cases']]}"
        if e["type"] == "fork":
            extra = f"forks {[n for n, _ in e['forks']]} join {e['join']}"
        nxt = e.get("next")
        ps = f.param_strings(e.get("params"))
        print(f"  {e['i']:3} {e['type']:<7} {str(e['name']):<10} next={'end' if nxt == END else nxt}  {extra}  {ps[:6] if ps else ''}")
