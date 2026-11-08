"""Print the readable strings of event flow files (bfevfl), to see their entry points, actors and actions.
Numbered placeholder names (Event12, ChoiceLabel3, ...) are left out.

usage: evstrings.py <name> [<name> ...]   (file names under romfs/Event/EventFlow, without .bfevfl.zs)
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nxfmt import ROMFS, unzs

for name in sys.argv[1:]:
    data = unzs((ROMFS / "Event" / "EventFlow" / f"{name}.bfevfl.zs").read_bytes())
    found = []
    for m in re.finditer(rb"[\x20-\x7e]{4,}", data):
        s = m.group().decode()
        if s not in found and not re.fullmatch(r"(Event|ChoiceLabel)\d+|.*[^\w./:()\-\s].*", s):
            found.append(s)
    print(f"===== {name} ({len(data)} bytes, {len(found)} strings)")
    print("  " + " | ".join(found))
