"""Patches to item actors (Pack/Actor/*.pack.zs) and the item table (RSDB/PouchActorInfo).

No free magic, tested 2026-11-08: the pack edit below alone did NOT stop the Lens giving magic. The game reads the
compiled table RSDB/PouchActorInfo, where each of these items' row has ExpansionAvility "MPMaxUP" and MPMax 24
(Great Fairy blessings: 24 / 48). rsdb_no_free_magic takes both keys out of those rows (not the Great Fairies'), so
they read like any other item. The pack edit is kept as well, so the two sources agree.

No free magic in the packs: the spells, the Lens of Truth and the magic arrows (and the bows and pouch items that hold them) carry
an ExpansionAbilityActor component, Item_FairyBless_MP ({ExpansionAbility, MPMax 24}), the same one the Great Fairy's
blessing (Item_FairyBless_MP / Pouch_FairyBless_MP) uses. So getting any of them gives the magic meter, a failsafe
for an order the normal game never allows. The patch takes the ExpansionAbilityActor entry out of those actors'
GameParameterTable, so only the Great Fairies give magic.

The component file itself (GameParameter/ExpansionAbilityActor/Item_FairyBless_MP...) is left alone: packs share it
by path, so changing it could also change the Great Fairy's. The GameParameterTable files are each item's own.

How: in the table's Components hash node, the entries after the removed one move up 8 bytes and the count drops by
one; the 8 bytes left at the node's end are zeroed and nothing points at them. No offset changes, the file and the pack
keep their size (so the size table needs nothing).

usage: itempatches.py   lists what the magic patch would change, writes nothing
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from nxfmt import ROMFS, Byml, parse_sarc, unzs
from bymledit import entries

MAGIC_COMPONENT = "ExpansionAbilityActor"
MAGIC_FILE = "GameParameter/ExpansionAbilityActor/Item_FairyBless_MP"   # also matches ..._MPDouble
GREAT_FAIRY = "FairyBless"   # actors that keep their magic


def magic_packs():
    """Pack paths (relative to romfs) of actors that give magic but aren't the Great Fairy's blessing."""
    out = []
    for p in sorted((ROMFS / "Pack" / "Actor").glob("*.pack.zs")):
        if GREAT_FAIRY in p.name:
            continue
        raw = unzs(p.read_bytes())
        if MAGIC_FILE.encode() in raw:
            out.append(p.relative_to(ROMFS).as_posix())
    return out


def _components_node(b):
    root = struct.unpack_from(b.e + "I", b.d, 12)[0]
    for key, t, pos in entries(b, root):
        if key == "Components" and t == 0xC1:
            return b.u32(pos)
    return None


def drop_magic_from_table(gpt):
    """A GameParameterTable without its ExpansionAbilityActor entry (same size), or None if it has none for magic."""
    b = Byml(gpt)
    node = _components_node(b)
    if node is None:
        return None
    ents = list(entries(b, node))
    idx = next((i for i, (k, _, _) in enumerate(ents) if k == MAGIC_COMPONENT), None)
    if idx is None:
        return None
    k, t, pos = ents[idx]
    assert t == 0xA0 and MAGIC_FILE in b.strs[b.u32(pos)], f"unexpected {MAGIC_COMPONENT}: {b.strs[b.u32(pos)]}"
    n = len(ents)
    out = bytearray(gpt)
    start = node + 4 + 8 * idx
    end = node + 4 + 8 * n
    out[start:end - 8] = gpt[start + 8:end]
    out[end - 8:end] = bytes(8)
    out[node + 1:node + 4] = (n - 1).to_bytes(3, "little" if b.e == "<" else "big")
    nb = Byml(bytes(out))
    comp = nb.root["Components"]
    want = {kk: v for kk, v in b.root["Components"].items() if kk != MAGIC_COMPONENT}
    assert comp == want, "Components changed beyond the magic entry"
    assert {kk: v for kk, v in nb.root.items() if kk != "Components"} == \
        {kk: v for kk, v in b.root.items() if kk != "Components"}
    return bytes(out)


POUCH_INFO = "RSDB/PouchActorInfo.Product.111.rstbl.byml.zs"
RSDB_MAGIC_KEYS = ("ExpansionAvility", "MPMax")   # "Avility": the game's spelling


def remove_keys(data, node, keys):
    """data with the given keys taken out of the BYML hash node at node, in place (same size: entries after a removed
    one move up 8 bytes, the count drops, the freed bytes at the node's end are zeroed; nothing points into them)."""
    b = Byml(data)
    out = bytearray(data)
    ents = list(entries(b, node))
    keep = [i for i, (k, _, _) in enumerate(ents) if k not in keys]
    first = node + 4
    packed = b"".join(data[first + 8 * i:first + 8 * i + 8] for i in keep)
    out[first:first + 8 * len(ents)] = packed + bytes(8 * (len(ents) - len(keep)))
    out[node + 1:node + 4] = len(keep).to_bytes(3, "little" if b.e == "<" else "big")
    return bytes(out)


def rsdb_no_free_magic(data):
    """PouchActorInfo with ExpansionAvility (MPMaxUP) and MPMax taken out of every row that has them, except the
    Great Fairy's blessings; (data, row names changed). This table, not the actor packs, is what the game reads."""
    b = Byml(data)
    root = struct.unpack_from(b.e + "I", data, 12)[0]
    assert data[root] == 0xC0, "PouchActorInfo changed: root is not an array"
    n = b.u24(root + 1)
    types = data[root + 4:root + 4 + n]
    base = root + 4 + ((n + 3) & ~3)
    out, changed = data, []
    for i in range(n):
        if types[i] != 0xC1:
            continue
        node = b.u32(base + 4 * i)
        row = b._node(0xC1, node)
        if row.get("ExpansionAvility") != "MPMaxUP" or GREAT_FAIRY in row.get("__RowId", ""):
            continue
        out = remove_keys(out, node, RSDB_MAGIC_KEYS)
        changed.append(row["__RowId"])
    before = {r["__RowId"]: r for r in Byml(data).root}
    after = {r["__RowId"]: r for r in Byml(out).root}
    assert before.keys() == after.keys()
    for name, r in before.items():
        want = {k: v for k, v in r.items() if k not in RSDB_MAGIC_KEYS} if name in changed else r
        assert after[name] == want, f"PouchActorInfo: {name} changed beyond its magic keys"
    return out, changed


def no_free_magic(data):
    """A pack with the magic entry taken out of every GameParameterTable in it that has one; (data, names changed)."""
    out = bytearray(data)
    changed = []
    for name, gpt in parse_sarc(data).items():
        if "GameParameterTable/" not in name:
            continue
        new = drop_magic_from_table(gpt)
        if new is None:
            continue
        at = data.find(gpt)
        assert at >= 0 and data.find(gpt, at + 1) < 0, f"{name}: not unique in its pack"
        out[at:at + len(gpt)] = new
        changed.append(name)
    out = bytes(out)
    check = parse_sarc(out)
    for name in changed:
        assert MAGIC_COMPONENT not in Byml(check[name]).root["Components"]
    return out, changed


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    _, rows = rsdb_no_free_magic(unzs((ROMFS / POUCH_INFO).read_bytes()))
    print(f"{POUCH_INFO}: {len(rows)} rows: {', '.join(rows)}")
    for rel in magic_packs():
        _, changed = no_free_magic(unzs((ROMFS / rel).read_bytes()))
        print(f"{rel}: {', '.join(c.split('/')[-1] for c in changed) or 'NOTHING (magic file present, no table entry)'}")
