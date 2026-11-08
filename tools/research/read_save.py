"""Look up game-data values in a save (progress.sav), which stores (murmur3 hash u32, value u32) pairs as TotK does.
Read-only. For an Enum the value is the murmur3 hash of the chosen name, printed as that name when it is a known one.

usage: read_save.py <progress.sav> <name> [<name> ...]
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gamedata import murmur3

ENUM_NAMES = ["NotReady", "Ready", "Step01", "Step02", "Step2_1", "Srep2_1_1", "Step2_2", "Step03", "Step04", "Step05",
              "Step06", "Complete", "Step01_00", "Step01_00_01", "Step01_00_02", "Step01_00_03", "Step01_01",
              "Step01_02"]
by_hash = {murmur3(n.encode()): n for n in ENUM_NAMES}

path, *names = sys.argv[1:]
d = Path(path).read_bytes()
print(f"{path}: {len(d)} bytes, magic {d[:8].hex(' ')}")
for name in names:
    h = murmur3(name.encode())
    hits = [i for i in range(0, len(d) - 8, 4) if struct.unpack_from("<I", d, i)[0] == h]
    if not hits:
        print(f"  {name}: not in the save (hash {h:#x})")
    for i in hits[:3]:
        v = struct.unpack_from("<I", d, i + 4)[0]
        print(f"  {name}: @{i:#x} value {v} {by_hash.get(v, '')}")
