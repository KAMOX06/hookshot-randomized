"""Read logic modules (AINB, version 0x408 here) far enough to print their nodes and how their inputs are wired.
Layout worked out from Logic/Market_Common_f252 (the Temple of Time door):

Header: node count u32 at +0x10, string pool +0x24, io params +0x34; nodes (0x3C each) follow the 0x74-byte header.
A node: type u16, index u16, ..., name string u32 at +8, body offset u32 at +0x14.
The io section starts with 12 table offsets, input and output interleaved per type:
  int in, int out, bool in, bool out, float in, float out, string in, string out, vec3f in, vec3f out,
  user-defined in, user-defined out.
A node body starts with 6 (base, count) pairs of immediate params, then 12 (base, count) pairs, one per io table.
An input entry (0x10): name u32, source node i16, source param i16, flags u32, value u32 (a string offset for
strings); source node -1 means "not linked: use the value". An output entry (4): name u32 (top bit a flag).

usage: ainb.py <path relative to romfs, or a file>
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from nxfmt import ROMFS

TYPES = ["int", "bool", "float", "string", "vec3f", "userdefined"]


class Ainb:
    def __init__(self, data):
        self.d = data
        assert data[:4] == b"AIB ", data[:4]
        self.version = struct.unpack_from("<I", data, 4)[0]
        self.n_commands = struct.unpack_from("<I", data, 0x0C)[0]   # 0 in logic modules; Sequence/AI files have them
        self.n_nodes = struct.unpack_from("<I", data, 0x10)[0]
        self.node_base = 0x74 + 0x18 * self.n_commands               # nodes follow the 0x18-byte commands
        self.strings = struct.unpack_from("<I", data, 0x24)[0]
        self.io = struct.unpack_from("<I", data, 0x34)[0]
        self.tables = struct.unpack_from("<12I", data, self.io)

    def s(self, off):
        p = self.strings + (off & 0x7FFFFFFF)
        return self.d[p:self.d.index(b"\0", p)].decode("utf-8", "replace")

    def node(self, i):
        o = self.node_base + 0x3C * i
        body = struct.unpack_from("<I", self.d, o + 0x14)[0]
        pairs = struct.unpack_from("<24I", self.d, body + 0x30)
        n = {"i": i, "name": self.s(struct.unpack_from("<I", self.d, o + 8)[0]), "inputs": [], "outputs": {}}
        for k, t in enumerate(TYPES):
            if t == "userdefined":
                continue   # different entry sizes; not needed here
            ib, ic = pairs[4 * k], pairs[4 * k + 1]
            ob, oc = pairs[4 * k + 2], pairs[4 * k + 3]
            for j in range(ib, ib + ic):
                p = self.tables[2 * k] + 0x10 * j
                name, src_n, src_p, flags, val = struct.unpack_from("<IhhII", self.d, p)
                n["inputs"].append({"type": t, "name": self.s(name), "src": (src_n, src_p), "flags": flags,
                                    "value": self.s(val) if t == "string" else struct.unpack("<i", struct.pack("<I", val))[0],
                                    "entry": p})
            n["outputs"][t] = [self.s(struct.unpack_from("<I", self.d, self.tables[2 * k + 1] + 4 * j)[0])
                               for j in range(ob, ob + oc)]
        return n

    def nodes(self):
        return [self.node(i) for i in range(self.n_nodes)]


if __name__ == "__main__":
    p = Path(sys.argv[1])
    a = Ainb((p if p.exists() else ROMFS / p).read_bytes())
    ns = a.nodes()
    print(f"version 0x{a.version:x}, {a.n_nodes} nodes")
    for n in ns:
        print(f"[{n['i']:2}] {n['name']}")
        for x in n["inputs"]:
            sn, sp = x["src"]
            if sn >= 0:
                outs = ns[sn]["outputs"].get(x["type"], [])
                link = f"<- [{sn}] {ns[sn]['name']}.{outs[sp] if 0 <= sp < len(outs) else f'#{sp}'}"
            else:
                link = f"= {x['value']!r}"
            print(f"      in  {x['type']:<6} {x['name']:<26} {link}")
        for t, outs in n["outputs"].items():
            for o in outs:
                print(f"      out {t:<6} {o}")
