"""Adding events (and actor actions) to a BFEVFL v4 event flow.

Layout used (evfl.py's, plus):
- header +0x18 RELT offset (u32), +0x1C file size. Flowchart (EVFL): event count u16 at +0x16, events pointer at +0x30.
- actor (0x38): actions pointer at +0x18, action count u16 at +0x30.
- event (0x30): name pointer +0; type u8 +8; action: next u16 +0xA, actor u16 +0xC, action u16 +0xE, params +0x10;
  switch: case count u16 +0xA, actor +0xC, query +0xE, params +0x10, cases pointer +0x18 (case: u32 value,
  u16 event, 2 pad).
- params container: u8 1, u8, u16 count, u32, DIC pointer +8, value pointers +0x10; value (0x18): type u8 (2 int,
  3 bool, 5 string), u8, u16 1, ..., data at +0x10 (u32 number, or a string pointer). Strings: u16 length, bytes,
  NUL (pointers point at the length).
- RELT: magic, self offset u32 (+4), section count u32 (+8); section (+0x10): pointer u64, offset u32 (+0x18),
  size u32 (+0x1C), first entry u32 (+0x20), entry count u32 (+0x24); entries (+0x28): (u32 offset, u32 mask), bit i
  meaning a pointer at offset + 8 * i.

Everything new goes in one block inserted where RELT starts: new strings, a copy of the whole event table with the
new events after it (the flowchart then points at the copy, the old table stays unused), new action tables, new
param containers and values, new case arrays. Pointers inside the block get RELT entries of their own (appended:
they lie after every older one). Nothing before the block moves.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

EVENT = 0x30
ACTOR = 0x38
END = 0xFFFF


class FlowEditor:
    def __init__(self, data):
        from evfl import Flow
        self.d = bytes(data)
        self.f = Flow(self.d)
        self.relt = struct.unpack_from("<I", self.d, 0x18)[0]
        assert self.d[self.relt:self.relt + 4] == b"RELT" and struct.unpack_from("<I", self.d, self.relt + 8)[0] == 1
        self.fc = self.f.fc
        self.events = [bytearray(self.d[self.f.events_ptr + EVENT * i:self.f.events_ptr + EVENT * (i + 1)])
                       for i in range(self.f.n_events)]
        self.block = bytearray()
        self.ptrs = []          # positions (in the block) of pointers to relocate
        self.actor_actions = {}  # actor index -> [action name pointers (absolute)]
        self.strings = {}
        self.names = 0
        self.patches = {}        # u16 link fields of the game's data (before the block) -> new event

    # -- the block --
    @property
    def base(self):
        return self.relt   # where the block starts in the output

    def _align(self, n=8):
        self.block += b"\0" * (-len(self.block) % n)

    def string(self, s):
        """Absolute pointer to string s (new, in the block)."""
        if s not in self.strings:
            self._align(2)
            self.strings[s] = self.base + len(self.block)
            e = struct.pack("<H", len(s.encode())) + s.encode() + b"\0"
            self.block += e + b"\0" * (len(e) % 2)
        return self.strings[s]

    def _put(self, blob, ptr_offsets=()):
        """blob placed 8-aligned in the block; ptr_offsets: its pointer fields. Returns its absolute offset."""
        self._align()
        at = len(self.block)
        self.block += blob
        self.ptrs += [at + p for p in ptr_offsets]
        return self.base + at

    # -- actors --
    def actor_index(self, name):
        return next(i for i, a in enumerate(self.f.actors()) if a["name"] == name)

    def action_index(self, actor, action, add=False):
        """Index of action in actor's action list; with add, appended when missing."""
        a = self.f.actors()[actor]
        names = a["actions"] + [n for n, _ in self.actor_actions.get(actor, [])[len(a["actions"]):]]
        if action in names:
            return names.index(action)
        assert add, f"actor {a['name']} has no action {action}"
        o = self.f.actors_ptr + ACTOR * actor
        if actor not in self.actor_actions:
            arr = struct.unpack_from("<Q", self.d, o + 0x18)[0]
            self.actor_actions[actor] = [(n, struct.unpack_from("<Q", self.d, arr + 8 * k)[0])
                                         for k, n in enumerate(a["actions"])]
        self.actor_actions[actor].append((action, self.string(action)))
        return len(self.actor_actions[actor]) - 1

    # -- params --
    def params_like(self, template_event, values):
        """A new params container shaped as template_event's (same keys, shared DIC), with values {key: value}:
        str -> a string, bool -> a bool, int -> an int (each key keeps the template's type)."""
        d = self.d
        p = self.f.event(template_event)["params"]
        n = struct.unpack_from("<H", d, p + 2)[0]
        dic = struct.unpack_from("<Q", d, p + 8)[0]
        keys = [self.f.s(struct.unpack_from("<Q", d, dic + 8 + 0x10 * (k + 1) + 8)[0]) for k in range(n)]
        assert set(values) <= set(keys), (values, keys)
        vals = []
        for k in range(n):
            v = struct.unpack_from("<Q", d, p + 0x10 + 8 * k)[0]
            raw = bytearray(d[v:v + 0x18])
            t = raw[0]
            ptr = []
            if keys[k] in values:
                x = values[keys[k]]
                if t == 5:
                    struct.pack_into("<Q", raw, 0x10, self.string(x))
                elif t == 3:
                    struct.pack_into("<I", raw, 0x10, 0x80000001 if x else 0)
                elif t == 2:
                    struct.pack_into("<i", raw, 0x10, x)
                else:
                    raise ValueError(f"param {keys[k]} type {t}")
            if t == 5:
                ptr = [0x10]
            vals.append(self._put(bytes(raw), ptr))
        head = bytearray(d[p:p + 0x10]) + b"".join(struct.pack("<Q", v) for v in vals)
        return self._put(bytes(head), [8] + [0x10 + 8 * k for k in range(n)])

    def params_with_types(self, template_event, values, types):
        """As params_like, with some keys' value type changed first: types {key: 2 / 3 / 5}."""
        d = self.d
        p = self.f.event(template_event)["params"]
        n = struct.unpack_from("<H", d, p + 2)[0]
        dic = struct.unpack_from("<Q", d, p + 8)[0]
        keys = [self.f.s(struct.unpack_from("<Q", d, dic + 8 + 0x10 * (k + 1) + 8)[0]) for k in range(n)]
        vals = []
        for k in range(n):
            v = struct.unpack_from("<Q", d, p + 0x10 + 8 * k)[0]
            raw = bytearray(d[v:v + 0x18])
            if keys[k] in types:
                raw[0] = types[keys[k]]
            t, x = raw[0], values.get(keys[k])
            if x is not None:
                if t == 5:
                    struct.pack_into("<Q", raw, 0x10, self.string(x))
                elif t == 3:
                    struct.pack_into("<I", raw, 0x10, 0x80000001 if x else 0)
                elif t == 2:
                    struct.pack_into("<i", raw, 0x10, x)
            vals.append(self._put(bytes(raw), [0x10] if t == 5 else []))
        head = bytearray(d[p:p + 0x10]) + b"".join(struct.pack("<Q", v) for v in vals)
        return self._put(bytes(head), [8] + [0x10 + 8 * k for k in range(n)])

    # -- events --
    def _name(self):
        self.names += 1
        return self.string(f"RandoEvent{self.names}")

    def add_action(self, actor, action, params, next_=END):
        e = bytearray(EVENT)
        struct.pack_into("<Q", e, 0, self._name())
        e[8] = 0
        struct.pack_into("<HHH", e, 0xA, next_, actor, action)
        struct.pack_into("<Q", e, 0x10, params)
        self.events.append(e)
        return len(self.events) - 1

    def add_switch(self, actor, query, params, cases):
        """cases: [(value, event index)]."""
        arr = self._put(b"".join(struct.pack("<IHH", v, ev, 0) for v, ev in cases))
        e = bytearray(EVENT)
        struct.pack_into("<Q", e, 0, self._name())
        e[8] = 1
        struct.pack_into("<HHH", e, 0xA, len(cases), actor, query)
        struct.pack_into("<Q", e, 0x10, params)
        struct.pack_into("<Q", e, 0x18, arr)
        self.events.append(e)
        return len(self.events) - 1

    def set_case(self, switch, case_index, event):
        """Point a switch's case at event (new events' case arrays live in the block)."""
        e = self.events[switch]
        arr = struct.unpack_from("<Q", e, 0x18)[0]
        if arr >= self.base:
            struct.pack_into("<H", self.block, arr - self.base + 8 * case_index + 4, event)
        else:
            raise ValueError("only new switches")

    def set_next(self, ev, nxt):
        e = self.events[ev]
        assert e[8] in (0, 3, 4), "no next field"
        struct.pack_into("<H", e, 0xA, nxt)

    def next_of(self, ev):
        return struct.unpack_from("<H", self.events[ev], 0xA)[0]

    def redirect(self, target, new):
        """Every link of the game's events (next, switch cases, fork branches and join, the cases of type 5: a call
        of an entry point of this flow with its results, case: u32 result hash, u16 event) and every entry point
        that goes to target now goes to new. Returns how many links changed."""
        n = 0
        for i in range(self.f.n_events):
            e, raw = self.f.event(i), self.events[i]
            t = raw[8]
            if t in (0, 3, 4) and struct.unpack_from("<H", raw, 0xA)[0] == target:
                struct.pack_into("<H", raw, 0xA, new)
                n += 1
            elif t == 1:
                for _, nxt, pos in e["cases"]:
                    if nxt == target:
                        self.patches[pos] = new
                        n += 1
            elif t == 2:
                for nxt, pos in e["forks"]:
                    if nxt == target:
                        self.patches[pos] = new
                        n += 1
                if struct.unpack_from("<H", raw, 0xC)[0] == target:
                    struct.pack_into("<H", raw, 0xC, new)
                    n += 1
            elif t == 5:
                arr = struct.unpack_from("<Q", raw, 0x18)[0]
                for k in range(struct.unpack_from("<H", raw, 0xA)[0]):
                    if struct.unpack_from("<H", self.d, arr + 8 * k + 4)[0] == target:
                        self.patches[arr + 8 * k + 4] = new
                        n += 1
        for name, (ev, pos) in self.f.entry_points().items():
            if ev == target:
                self.patches[pos] = new
                n += 1
        return n

    def reloc_positions(self, d=None):
        """Every file position the RELT lists as a pointer."""
        d = d or self.d
        r = struct.unpack_from("<I", d, 0x18)[0]
        count = struct.unpack_from("<I", d, r + 0x24)[0]
        out = set()
        for k in range(count):
            off, mask = struct.unpack_from("<II", d, r + 0x28 + 8 * k)
            for b in range(32):
                if mask >> b & 1:
                    out.add(off + 8 * b)
        return out

    # -- output --
    def build(self):
        # action tables and the event table, last in the block
        for actor, acts in sorted(self.actor_actions.items()):
            arr = self._put(b"".join(struct.pack("<Q", p) for _, p in acts), [8 * k for k in range(len(acts))])
            self.actor_actions[actor] = (arr, len(acts))
        old_ptrs = self.reloc_positions()
        ep = self.f.events_ptr
        table_ptrs = []
        for i, e in enumerate(self.events):
            if i < self.f.n_events:   # (an old event: the fields the game's RELT relocates there)
                table_ptrs += [p - ep for p in range(ep + EVENT * i, ep + EVENT * (i + 1), 8) if p in old_ptrs]
            else:                     # (a new one: name, params, and a switch's cases)
                table_ptrs += [EVENT * i + o for o in ((0, 0x10, 0x18) if e[8] == 1 else (0, 0x10))]
        table = self._put(b"".join(bytes(e) for e in self.events), table_ptrs)
        self._align()
        n = len(self.block)
        d, relt = self.d, self.relt
        out = bytearray(d[:relt]) + self.block + bytearray(d[relt:])
        for pos, ev in self.patches.items():
            assert pos < relt and not (self.f.events_ptr <= pos < self.f.events_ptr + EVENT * self.f.n_events)
            struct.pack_into("<H", out, pos, ev)
        struct.pack_into("<Q", out, self.fc + 0x30, table)
        struct.pack_into("<H", out, self.fc + 0x16, len(self.events))
        for actor, (arr, count) in self.actor_actions.items():
            o = self.f.actors_ptr + ACTOR * actor
            struct.pack_into("<Q", out, o + 0x18, arr)
            struct.pack_into("<H", out, o + 0x30, count)
        r = relt + n
        count = struct.unpack_from("<I", out, r + 0x24)[0]
        entries = []
        ptrs = sorted(set(self.ptrs))
        k = 0
        while k < len(ptrs):
            start, mask = ptrs[k], 0
            while k < len(ptrs) and ptrs[k] - start < 8 * 32:
                assert (ptrs[k] - start) % 8 == 0
                mask |= 1 << ((ptrs[k] - start) // 8)
                k += 1
            entries.append(struct.pack("<II", relt + start, mask))
        first_free = r + 0x28 + 8 * count
        assert first_free <= len(out), "RELT: entries past the end"
        tail = bytes(out[first_free:])
        out = out[:first_free] + b"".join(entries) + tail
        struct.pack_into("<I", out, 0x18, r)
        struct.pack_into("<I", out, r + 4, r)
        struct.pack_into("<I", out, r + 0x1C, struct.unpack_from("<I", out, r + 0x1C)[0] + n)
        struct.pack_into("<I", out, r + 0x24, count + len(entries))
        struct.pack_into("<I", out, 0x1C, len(out))
        return bytes(out)
