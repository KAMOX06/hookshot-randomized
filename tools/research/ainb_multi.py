"""Print the sources behind every multi input (BoolMulti etc.) of a logic module.

A multi input's source is (-100 - first entry, count); its sources are consecutive (node i16, param i16, flags u32)
entries of the multi section (offset in the header at +0x38).

usage: ainb_multi.py <path relative to romfs, or a file>
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nxfmt import ROMFS
from ainb import Ainb

p = Path(sys.argv[1])
d = (p if p.exists() else ROMFS / p).read_bytes()
a = Ainb(d)
ns = a.nodes()
multi = struct.unpack_from("<I", d, 0x38)[0]
for n in ns:
    for x in n["inputs"]:
        sn, count = x["src"]
        if sn > -100:
            continue
        first = -100 - sn
        print(f"[{n['i']}] {n['name']}.{x['name']}:")
        for k in range(first, first + count):
            pos = multi + 8 * k
            node, param = struct.unpack_from("<hh", d, pos)
            outs = ns[node]["outputs"].get(x["type"], [])
            print(f"     entry {k} @0x{pos:x}: [{node}] {ns[node]['name']}.{outs[param] if param < len(outs) else param}")
