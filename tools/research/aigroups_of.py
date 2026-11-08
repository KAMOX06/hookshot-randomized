"""List the AI groups (logic modules) of a map file that reference an actor, and the actor's own entry.

usage: aigroups_of.py <map file relative to romfs> <actor hash>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nxfmt import ROMFS, load_byml

rel, h = sys.argv[1], int(sys.argv[2])
root = load_byml(ROMFS / rel)
actor = next((a for a in root["Actors"] if a.get("Hash") == h), None)
print("actor:", {k: v for k, v in (actor or {}).items() if k not in ("Phive",)})
groups = [g for g in root.get("AiGroups", []) or [] if any(r.get("Reference") == h for r in g.get("References", []))]
print(f"{len(groups)} AI group(s) reference it")
for g in groups:
    print("  logic:", g.get("Logic"), "| refs:", [r.get("Id") for r in g.get("References", [])][:12])
