"""Editing helpers for the game's files: zstd in and out with the game's dictionaries, and in-place BYML edits.

BYML here comes in both byte orders: the Banc maps are little-endian (YB), GameDataList is big-endian (BY).
Every write goes through b.e, the file's own order.
"""
import binascii
import bisect
import collections
import struct
import sys
from pathlib import Path

import compression.zstd as zstd

sys.path.insert(0, str(Path(__file__).parent))
from nxfmt import ROMFS, Byml, dicts

CONTAINERS = (0xC0, 0xC1, 0x20, 0x21)
OFFSET_VALUES = (0xA1, 0xD4, 0xD5, 0xD6)


def read_original(rel):
    """(decompressed data, the zstd dictionary it was packed with or None) of a game file."""
    raw = (ROMFS / rel).read_bytes()
    try:
        return zstd.decompress(raw), None
    except zstd.ZstdError:
        pass
    for d in dicts().values():
        try:
            return zstd.decompress(raw, zstd_dict=d), d
        except zstd.ZstdError:
            continue
    raise ValueError(f"no dictionary fits {rel}")


def write_zs(rel, out_dir, data, zdict):
    packed = zstd.compress(data, level=19, zstd_dict=zdict) if zdict else zstd.compress(data, level=19)
    assert (zstd.decompress(packed, zstd_dict=zdict) if zdict else zstd.decompress(packed)) == data
    dst = Path(out_dir) / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(packed)
    return dst


def entries(b, off):
    """(key, type, value field position) of each entry of the hash node at off."""
    for i in range(b.u24(off + 1)):
        e = off + 4 + 8 * i
        yield b.keys[b.u24(e)], b.d[e + 3], e + 4


class Walk:
    """Every u32 field of a BYML file that holds an offset, and every one that holds a value-string index."""

    def __init__(self, b):
        self.b = b
        self.offset_fields = []
        self.string_fields = []
        self.seen = set()
        root = b.u32(12)
        if root:
            self.node(b.d[root], root)

    def value(self, t, pos):
        if t == 0xA0:
            self.string_fields.append(pos)
        elif t in OFFSET_VALUES:
            self.offset_fields.append(pos)
        elif t in CONTAINERS:
            self.offset_fields.append(pos)
            self.node(t, self.b.u32(pos))

    def node(self, t, off):
        if off in self.seen:
            return
        self.seen.add(off)
        b = self.b
        n = b.u24(off + 1)
        if t == 0xC0:
            base = off + 4 + ((n + 3) & ~3)
            for i in range(n):
                self.value(b.d[off + 4 + i], base + 4 * i)
        elif t == 0xC1:
            for i in range(n):
                e = off + 4 + 8 * i
                self.value(b.d[e + 3], e + 4)
        elif t in (0x20, 0x21):
            ent = (4 if t == 0x20 else 8) + 4
            types = off + 4 + n * ent
            for i in range(n):
                self.value(b.d[types + i], off + 4 + ent * i + ent - 4)
        else:
            raise ValueError(f"node type {t:02x} at 0x{off:x}")


def string_table(strs):
    """A little-endian BYML string table node for strs, padded to 8 bytes."""
    n = len(strs)
    head = 4 + 4 * (n + 1)
    body, offs = b"", []
    for s in strs:
        offs.append(head + len(body))
        body += s.encode() + b"\0"
    offs.append(head + len(body))
    out = bytes([0xC2]) + n.to_bytes(3, "little") + b"".join(struct.pack("<I", o) for o in offs) + body
    return out + b"\0" * (-len(out) % 8)


def add_strings(data, new):
    """data with every string of new added to the value-string table (kept sorted). Everything after the table
    moves down; every offset that pointed past it, and every string index, is fixed."""
    b = Byml(data)
    assert b.e == "<", "only little-endian BYML handled"
    new = sorted(set(new) - set(b.strs))
    if not new:
        return data
    walk = Walk(b)
    st = b.u32(8)
    n = b.u24(st + 1)
    old_end = data.index(b"\0", st + b.u32(st + 4 + 4 * (n - 1))) + 1
    nxt = min([v for v in (b.u32(4), b.u32(12)) if v > st] + [b.u32(p) for p in walk.offset_fields if b.u32(p) > st])
    assert nxt >= old_end, "something sits inside the string table"
    strs = sorted(b.strs + new)
    remap = [bisect.bisect_left(strs, s) for s in b.strs]
    table = string_table(strs)
    delta = st + len(table) - nxt
    if delta % 8:   # keep everything after the table at its 8-byte alignment
        table += b"\0" * (-delta % 8)
        delta = st + len(table) - nxt
    out = bytearray(data[:st] + table + data[nxt:])
    moved = lambda pos: pos + delta if pos >= nxt else pos
    for hp in (4, 8, 12):
        v = struct.unpack_from("<I", data, hp)[0]
        if v >= nxt:
            struct.pack_into("<I", out, hp, v + delta)
    for p in walk.offset_fields:
        v = struct.unpack_from("<I", data, p)[0]
        struct.pack_into("<I", out, moved(p), v + delta if v >= nxt else v)
    for p in walk.string_fields:
        struct.pack_into("<I", out, moved(p), remap[struct.unpack_from("<I", data, p)[0]])
    return bytes(out)


def chest_dynamic(b, want_hash):
    """(position of the actor's Dynamic offset field, offset of its Dynamic node) for the actor with Hash want_hash."""
    fields = dict((k, p) for k, t, p in entries(b, b.u32(12)))
    arr = b.u32(fields["Actors"])
    m = b.u24(arr + 1)
    base = arr + 4 + ((m + 3) & ~3)
    for j in range(m):
        if b.d[arr + 4 + j] != 0xC1:
            continue
        actor = b.u32(base + 4 * j)
        af = {k: p for k, t, p in entries(b, actor)}
        if "Hash" in af and struct.unpack_from(b.e + "Q", b.d, b.u32(af["Hash"]))[0] == want_hash:
            return af["Dynamic"], b.u32(af["Dynamic"])
    raise KeyError(f"actor {want_hash} not found")


def apply_items(data, items, drop_keys=None):
    """data with each chest Hash -> item of items set as its Drop__DropActor, and, for each Hash in drop_keys, the
    named keys (such as GetEventName) taken out of that chest's Dynamic node.

    The BYML writer shares identical nodes, so chests with the same settings point at one Dynamic node (Mido's three
    blue-rupee chests do); a chest whose node is shared gets its own copy, at the end of the file, before the edit.
    A chest that loses keys always gets a new, shorter copy there (hash node entries stay in key order)."""
    drop_keys = drop_keys or {}
    data = add_strings(data, items.values())
    b = Byml(data)
    walk = Walk(b)
    users = collections.Counter(b.u32(p) for p in walk.offset_fields)
    out = bytearray(data)
    for h, item in items.items():
        pos, dyn = chest_dynamic(b, h)
        gone = set(drop_keys.get(h, ()))
        keys = [k for k, t, p in entries(b, dyn)]
        if gone or users[dyn] > 1:
            kept = [i for i, k in enumerate(keys) if k not in gone]
            out += b"\0" * (-len(out) % 8)
            copy = len(out)
            out += bytes([0xC1]) + len(kept).to_bytes(3, "little")
            out += b"".join(data[dyn + 4 + 8 * i:dyn + 12 + 8 * i] for i in kept)
            struct.pack_into("<I", out, pos, copy)
            users[dyn] -= 1
            field = copy + 4 + 8 * [keys[i] for i in kept].index("Drop__DropActor") + 4
        else:
            field = dyn + 4 + 8 * keys.index("Drop__DropActor") + 4
        struct.pack_into("<I", out, field, b.strs.index(item))
    out += b"\0" * (-len(out) % 4)
    return bytes(out)


def set_bool_defaults(data, flags_by_hash):
    """GameDataList data with the DefaultValue of Bool flags (murmur3 hash -> (name, value)) set, in place."""
    b = Byml(data)
    out = bytearray(data)
    gdl = dict((k, p) for k, t, p in entries(b, b.u32(12)))
    bools = b.u32(dict((k, p) for k, t, p in entries(b, b.u32(gdl["Data"])))["Bool"])
    n = b.u24(bools + 1)
    base = bools + 4 + ((n + 3) & ~3)
    done = set()
    for i in range(n):
        f = {k: (t, p) for k, t, p in entries(b, b.u32(base + 4 * i))}
        h = b.u32(f["Hash"][1])
        if h in flags_by_hash:
            t, p = f["DefaultValue"]
            assert t == 0xD0
            name, value = flags_by_hash[h]
            print(f"  flag {name}: default {bool(b.u32(p))} -> {value}")
            struct.pack_into(b.e + "I", out, p, int(value))
            done.add(h)
    missing = [flags_by_hash[h][0] for h in flags_by_hash if h not in done]
    assert not missing, f"flags not found: {missing}"
    return bytes(out)


def set_int_defaults(data, values_by_hash):
    """GameDataList data with the DefaultValue of Int entries (murmur3 hash -> (name, value)) set, in place."""
    b = Byml(data)
    out = bytearray(data)
    gdl = dict((k, p) for k, t, p in entries(b, b.u32(12)))
    ints = b.u32(dict((k, p) for k, t, p in entries(b, b.u32(gdl["Data"])))["Int"])
    n = b.u24(ints + 1)
    base = ints + 4 + ((n + 3) & ~3)
    done = set()
    for i in range(n):
        f = {k: (t, p) for k, t, p in entries(b, b.u32(base + 4 * i))}
        h = b.u32(f["Hash"][1])
        if h in values_by_hash:
            t, p = f["DefaultValue"]
            assert t == 0xD1
            name, value = values_by_hash[h]
            old = struct.unpack_from(b.e + "i", data, p)[0]
            print(f"  game data {name}: default {old} -> {value}")
            struct.pack_into(b.e + "i", out, p, int(value))
            done.add(h)
    missing = [values_by_hash[h][0] for h in values_by_hash if h not in done]
    assert not missing, f"Int entries not found: {missing}"
    return bytes(out)


def raise_sizes(table, growth):
    """ResourceSizeTable data with the entry of each resource path (without .zs) raised by its growth in bytes."""
    t = bytearray(table)
    assert t[:6] == b"RESTBL"
    str_size, ncrc, nname = struct.unpack_from("<III", t, 10)
    crc_pos = {struct.unpack_from("<I", t, 22 + 8 * i)[0]: 26 + 8 * i for i in range(ncrc)}
    for path, g in growth.items():
        if g:
            p = crc_pos[binascii.crc32(path.encode())]
            struct.pack_into("<I", t, p, struct.unpack_from("<I", t, p)[0] + g)
    return bytes(t)
