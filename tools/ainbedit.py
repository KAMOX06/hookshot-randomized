"""Add a SeqOneShotPushToPouch node to an AINB 0x408 sequence (the game's own "put an item in the pouch" step), as
used by Sequence/SetPouchAndStatusForGameStart.module.ainb for the starting wallet and tunics.

Layout (worked out from that file):
  header 0x74; commands (0x18 each) follow, then the node table (0x3C each), then: global params, node bodies,
  immediate params (header of 6 table offsets; entries 12 bytes), io params (header of 12 table offsets, in/out
  interleaved per type: int, bool, float, string, vec3f, userdefined; input entries 16 bytes), small sections,
  string pool (last).
  node entry: type u16, index u16, ..., name +8, name hash +0xC, body offset +0x14, ..., GUID +0x2C (16 bytes).
  node body: 6 (base, count) immediate pairs (int, bool, ...); at +0x30 12 io (base, count) pairs, 4 u32 per type
  (in base, in count, out base, out count); at +0x90 10 (count u8, base u8) link-type pairs; at +0xA4 one u32 file
  offset per link, then the link entries (node index u32, string u32).
  In SetPouchAndStatusForGameStart, node 1 is the sequence of the pushes (nodes 7, 8, 12, 13, link type 2).

Every field holding a file offset is moved when bytes are inserted before what it points at (insert()).
"""
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

HEADER_OFFSETS = [0x20, 0x24, 0x28, 0x2C, 0x30, 0x34, 0x38, 0x3C, 0x40, 0x44, 0x48, 0x4C, 0x50, 0x54, 0x58, 0x5C,
                  0x68, 0x6C, 0x70]   # zero means "none" and stays zero
NODE = 0x3C
LINK_TYPES = 10


def u32(d, o):
    return struct.unpack_from("<I", d, o)[0]


class Layout:
    def __init__(self, d):
        assert d[:4] == b"AIB " and u32(d, 4) == 0x408, "not an AINB 0x408"
        self.d = d
        self.n_cmd, self.n_node = u32(d, 0x0C), u32(d, 0x10)
        self.node_base = 0x74 + 0x18 * self.n_cmd
        self.pool = u32(d, 0x24)
        self.imm = u32(d, 0x2C)
        self.io = u32(d, 0x34)
        self.imm_tables = list(struct.unpack_from("<6I", d, self.imm))
        self.io_tables = list(struct.unpack_from("<12I", d, self.io))

    def node(self, i):
        return self.node_base + NODE * i

    def body(self, i):
        return u32(self.d, self.node(i) + 0x14)

    def links(self, i):
        b = self.body(i)
        pairs = [(self.d[b + 0x90 + 2 * k], self.d[b + 0x91 + 2 * k]) for k in range(LINK_TYPES)]
        return pairs, sum(c for c, _ in pairs)

    def string(self, off):
        p = self.pool + off
        return self.d[p:self.d.index(b"\0", p)].decode()

    def offset_fields(self):
        """Positions of every u32 that holds a file offset."""
        out = [p for p in HEADER_OFFSETS if u32(self.d, p)]
        out += [self.node(i) + 0x14 for i in range(self.n_node)]
        out += [self.imm + 4 * k for k in range(6)]
        out += [self.io + 4 * k for k in range(12)]
        for i in range(self.n_node):
            _, total = self.links(i)
            out += [self.body(i) + 0xA4 + 4 * k for k in range(total)]
        return out


def insert(d, pos, blob):
    """d with blob inserted at pos; every offset field pointing at pos or later moves by len(blob)."""
    lay = Layout(d)
    out = bytearray(d)
    for f in lay.offset_fields():
        v = u32(d, f)
        if v >= pos:
            struct.pack_into("<I", out, f, v + len(blob))
    return bytes(out[:pos]) + blob + bytes(out[pos:])


def add_push_to_pouch(d, actor_path, num, template=7, parent=1):
    """d with a new SeqOneShotPushToPouch node (a copy of node `template`) pushing actor_path num times, linked last
    in sequence node `parent`. Returns (data, new node index)."""
    lay = Layout(d)
    tb = lay.body(template)
    t_entry = d[lay.node(template):lay.node(template) + NODE]
    t_body = d[tb:tb + 0xA4]
    name = lay.string(u32(t_entry, 8))
    assert name == "SeqOneShotPushToPouch", f"node {template} is {name}"
    assert lay.links(template)[1] == 0, "template node has links"
    t_int_in, t_int_n = struct.unpack_from("<II", t_body, 0x30)
    t_str_in, t_str_n = struct.unpack_from("<II", t_body, 0x30 + 16 * 3)
    t_bool_imm, t_bool_imm_n = struct.unpack_from("<II", t_body, 8)
    assert (t_int_n, t_str_n, t_bool_imm_n) == (1, 1, 1), "template node's params changed"
    assert all(struct.unpack_from("<I", t_body, 0x30 + 16 * k + 4)[0] == 0 for k in (1, 2, 4, 5)), "template inputs"
    pairs, total = lay.links(parent)
    linked = [u32(d, u32(d, lay.body(parent) + 0xA4 + 4 * k)) for k in range(total)]
    assert template in linked, f"node {template} is not under node {parent}"
    link_type = next(k for k, (c, _) in enumerate(pairs) if c)
    assert sum(1 for c, _ in pairs if c) == 1, "parent has several link types"
    new = lay.n_node

    # 1. the string, at the end of the pool (the pool is the last section)
    assert lay.pool > max(lay.io_tables), "string pool is not last"
    str_off = len(d) - lay.pool
    d = d + actor_path.encode() + b"\0"

    # 2. inputs: one int (Num) at the end of the int-in table, one string (ActorName) at the end of the string-in table
    lay = Layout(d)
    int_entry = bytearray(d[lay.io_tables[0] + 16 * t_int_in:][:16])
    struct.pack_into("<i", int_entry, 12, num)
    int_index = (lay.io_tables[1] - lay.io_tables[0]) // 16
    d = insert(d, lay.io_tables[1], bytes(int_entry))
    lay = Layout(d)
    str_entry = bytearray(d[lay.io_tables[6] + 16 * t_str_in:][:16])
    struct.pack_into("<I", str_entry, 12, str_off)
    str_index = (lay.io_tables[7] - lay.io_tables[6]) // 16
    d = insert(d, lay.io_tables[7], bytes(str_entry))

    # 3. its bool immediate (a copy of the template's), at the end of the bool immediate table
    lay = Layout(d)
    imm_entry = d[lay.imm_tables[1] + 12 * t_bool_imm:][:12]
    bool_index = (lay.imm_tables[2] - lay.imm_tables[1]) // 12
    d = insert(d, lay.imm_tables[2], imm_entry)

    # 4. the node's body, after the last body (= where the immediate section starts)
    lay = Layout(d)
    body = bytearray(t_body)
    struct.pack_into("<I", body, 8, bool_index)
    struct.pack_into("<I", body, 0x30, int_index)
    struct.pack_into("<I", body, 0x30 + 16 * 3, str_index)
    body_pos = lay.imm
    d = insert(d, body_pos, bytes(body))

    # 5. the node entry, after the last one (= where the global params start)
    lay = Layout(d)
    entry = bytearray(t_entry)
    struct.pack_into("<H", entry, 2, new)
    entry[0x2C:0x3C] = os.urandom(16)   # GUID
    entry_pos = lay.node(new)
    d = insert(d, entry_pos, bytes(entry))
    out = bytearray(d)
    struct.pack_into("<I", out, 0x10, new + 1)
    struct.pack_into("<I", out, entry_pos + 0x14, body_pos + NODE)   # the body moved with the entry insert
    d = bytes(out)

    # 6. the link: one more offset in the parent's offset list, one more entry at the end of its body
    lay = Layout(d)
    pb = lay.body(parent)
    link_entry = d[u32(d, pb + 0xA4):][:8]   # copy of its first link entry (node index, string)
    d = insert(d, pb + 0xA4 + 4 * total, b"\0\0\0\0")
    lay = Layout(d)
    pb = lay.body(parent)
    end_of_body = lay.body(parent + 1) if parent + 1 < lay.n_node else lay.imm
    d = insert(d, end_of_body, struct.pack("<I", new) + link_entry[4:])
    out = bytearray(d)
    struct.pack_into("<I", out, pb + 0xA4 + 4 * total, end_of_body)
    for k in range(LINK_TYPES):
        c, base = out[pb + 0x90 + 2 * k], out[pb + 0x91 + 2 * k]
        if k == link_type:
            out[pb + 0x90 + 2 * k] = c + 1
        elif k > link_type:
            out[pb + 0x91 + 2 * k] = base + 1
    return bytes(out), new


IMM_SIZE = 12   # int and bool immediate entries (the only kinds this file uses)


def summary(d):
    """Per node: name, inputs (name, source, value; strings resolved), outputs, immediate entries, links."""
    from ainb import Ainb
    lay = Layout(d)
    a = Ainb(d)
    out = []
    for i in range(lay.n_node):
        n = a.node(i)
        b = lay.body(i)
        imm = []
        for k in range(2):
            base, count = struct.unpack_from("<II", d, b + 8 * k)
            imm += [d[lay.imm_tables[k] + IMM_SIZE * j:][:IMM_SIZE] for j in range(base, base + count)]
        _, total = lay.links(i)
        links = [(u32(d, u32(d, b + 0xA4 + 4 * k)), lay.string(u32(d, u32(d, b + 0xA4 + 4 * k) + 4)))
                 for k in range(total)]
        out.append((n["name"], [(x["type"], x["name"], x["src"], x["flags"], x["value"]) for x in n["inputs"]],
                    n["outputs"], imm, links))
    return out


def add_pushes(d, pushes):
    """d with one push node per (actor_path, num), linked in that order after the existing pushes."""
    for actor_path, num in pushes:
        d, _ = add_push_to_pouch(d, actor_path, num)
    return d


def check_push(original, patched, actor_path, num, parent=1):
    return check_pushes(original, patched, [(actor_path, num)], parent)


def check_pushes(original, patched, pushes, parent=1):
    """Problems (list of str) if patched isn't original plus exactly these pushes, in order, at the end of parent."""
    a, b = summary(original), summary(patched)
    problems = []
    k = len(pushes)
    if len(b) != len(a) + k:
        return [f"node count {len(a)} -> {len(b)}"]
    for i, (x, y) in enumerate(zip(a, b)):
        if i == parent:
            if x[:4] != y[:4] or y[4][:-k] != x[4] or [n for n, _ in y[4][-k:]] != list(range(len(a), len(a) + k)):
                problems.append(f"node {i}: changed beyond the new links")
        elif x != y:
            problems.append(f"node {i}: changed")
    for (actor_path, num), (name, inputs, _, _, links) in zip(pushes, b[len(a):]):
        want = {("int", "Num"): num, ("string", "ActorName"): actor_path}
        got = {(t, n): v for t, n, src, _, v in inputs if src[0] < 0}
        if name != "SeqOneShotPushToPouch" or got != want or links:
            problems.append(f"new node: {name} {got}")
    la, lb = Layout(original), Layout(patched)
    if patched[lb.pool:lb.pool + len(original) - la.pool] != original[la.pool:]:
        problems.append("string pool changed")
    return problems


if __name__ == "__main__":
    from nxfmt import ROMFS
    from ainb import Ainb
    rel = "Sequence/SetPouchAndStatusForGameStart.module.ainb"
    a = (ROMFS / rel).read_bytes()
    b, n = add_push_to_pouch(a, "Work/Actor/Item_DekuNut.engine__actor__ActorParam.gyml", 0)
    print(f"{len(a)} -> {len(b)} bytes, new node {n}")
    print("check:", check_push(a, b, "Work/Actor/Item_DekuNut.engine__actor__ActorParam.gyml", 0) or "OK")
    for node in Ainb(b).nodes():
        ins = ", ".join(f"{x['name']}={x['value']!r}" for x in node["inputs"])
        print(f"  [{node['i']:2}] {node['name']:<45} {ins}")
    lay = Layout(b)
    for i in range(lay.n_node):
        pairs, total = lay.links(i)
        if total:
            print(f"  node {i} links:", [u32(b, u32(b, lay.body(i) + 0xA4 + 4 * k)) for k in range(total)])
