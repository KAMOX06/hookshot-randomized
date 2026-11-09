"""Crediting a dungeon for its map, compass or boss key found outside it (option "Keys, maps and compasses anywhere").

How the game counts them (found 2026-11-09): Item_Dungeon_Map / _Compass / _BossKey / _SmallKey are one actor each,
shared by every dungeon, and the compiled pickup code credits whatever dungeon the player is in. Each dungeon's own
record is game data: DungeonInfo.<Dungeon>.IsGetMap, IsGetCompass, IsGetBossKey (Bool) and SmallKeyNum (Int). Found
in a field area (Kokiri Forest in seed 1217) they count for nothing; found in another dungeon they'd count for it.

The game also sets those flags from map logic: a logic module is wired to a placed actor through the map file's
AiGroups (Logic: the module, References: Id = the module's InstanceName, Reference = the actor's Hash). Six modules
are just "chest opened -> set a Bool": ActorLogicTreasureBox (output 3 OpenState) driving TriggerGameDataSetBoolTrue.
So a chest holding another dungeon's map gets a copy of one of them (FireTemple_Level_Room_13_Prefab_a00f, which
sets FireTemple_GetHammer) setting DungeonInfo.<its dungeon>.IsGetMap instead, and an AiGroup tying it to the chest.

- The copy: the module's own name (first string of the pool) becomes Rando_DungeonCredit_Prefab_<9 hex>, the same
  length, so nothing moves. The flag name is the pool's last string, at the end of the file: it is cut off and the new
  one written in its place. The InstanceName stays (Obj_TBox_A_02_61b5): it only has to match the AiGroup's Id.
- Setting a Bool again is harmless, so the trigger firing on every visit doesn't matter. Small keys are a count
  (SmallKeyNum, add 1), which could be added again on every visit: not done yet, they stay in their own dungeon.
- These items are only placed in their own dungeon or in field areas (randomize.shuffle): in another dungeon the
  pickup itself would credit that one too.
"""
import hashlib
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from nxfmt import ROMFS

TEMPLATE = "Logic/FireTemple_Level_Room_13_Prefab_a00f.logic.module.ainb"
TEMPLATE_NAME = "FireTemple_Level_Room_13_Prefab_a00f"
TEMPLATE_FLAG = "FireTemple_GetHammer"
INSTANCE = "Obj_TBox_A_02_61b5"
NAME_PREFIX = "Rando_DungeonCredit_Prefab"   # + "_" + 9 hex = 36 characters, as the template's own name
FLAGS = {"Item_Dungeon_Map": "IsGetMap", "Item_Dungeon_Compass": "IsGetCompass", "Item_Dungeon_BossKey": "IsGetBossKey"}
SIZE_MARGIN = 1024   # (the game's logic entries are their size + ~390)


def flag_for(item, dungeon):
    return f"DungeonInfo.{dungeon}.{FLAGS[item]}"


def module_name(chest_hash):
    n = f"{NAME_PREFIX}_{int(hashlib.sha1(str(chest_hash).encode()).hexdigest()[:9], 16):09x}"
    assert len(n) == len(TEMPLATE_NAME)
    return n


def module_path(name):
    """(romfs file, the AiGroup's Logic path) of a module."""
    return f"Logic/{name}.logic.module.ainb", f"Logic/Module/{NAME_PREFIX}/{name}.logic.module.ain"


def make_module(name, flag):
    """The template module renamed to name, setting flag."""
    from ainb import Ainb
    d = (ROMFS / TEMPLATE).read_bytes()
    a = Ainb(d)
    pool = a.strings
    first = (TEMPLATE_NAME + ".logic.module").encode()
    assert d[pool:pool + len(first) + 1] == first + b"\0", "logic template changed: its name"
    last = TEMPLATE_FLAG.encode() + b"\0"
    assert d.endswith(last) and d.count(last) == 1, "logic template changed: its flag is not the last string"
    assert [a.node(i)["name"] for i in range(a.n_nodes)] == ["ActorLogicTreasureBox", "TriggerGameDataSetBoolTrue"]
    assert a.node(1)["inputs"][1]["src"] == (0, 3) and a.node(1)["inputs"][2]["value"] == TEMPLATE_FLAG
    assert len(d) not in struct.unpack_from(f"<{pool // 4}I", d, 0), "logic template: something holds the file size"
    out = bytearray(d[:len(d) - len(last)])
    out[pool:pool + len(first)] = (name + ".logic.module").encode()
    out += flag.encode() + b"\0"
    b = Ainb(bytes(out))
    assert b.node(1)["inputs"][2]["value"] == flag and b.node(0)["name"] == "ActorLogicTreasureBox"
    return bytes(out)


# NOT IN USE: Int triggers built this way never ran in game (2026-11-09, seeds 1220-1224, values read from the saves):
# AddInt on DungeonInfo.ForestTemple.SmallKeyNum and on PouchContentMaxNum.Arrow, AddInt with the game's own node
# GUID, SetIntSum (5) on PouchContentMaxNum.DekuSeed: all stayed 0, while the Bool version of the same module works
# (seed 1218; a map picked up in Kokiri Forest without it doesn't credit the Deku Tree, seed 1217). The files match
# the game's layout as far as ainb.py reads it; something not yet understood is missing.
# Small keys: the same template with its trigger turned into TriggerGameDataAddInt (AddValue 1) on
# DungeonInfo.<Dungeon>.SmallKeyNum, laid out as the game's own (BottomOfTheWell_Level_Room_10_Prefab_67f9 node 3):
# type hash 0x4a57e096 at +0xC in the node entry; int inputs AddValue (1) then GameDataArrayIndex;
# the string input named GameDataNameInt. Whether the trigger adds again when the opened chest's map loads is what
# the first test seed checks (open the key chest, then leave and come back a few times).
KEY_FLAG = "SmallKeyNum"
ADDINT_HASH = 0x4A57E096


def key_flag(dungeon):
    return f"DungeonInfo.{dungeon}.{KEY_FLAG}"


SETINTSUM_HASH = 0x825D47F7
ADDINT_GUID = bytes.fromhex("351c7fa2f9464a42a2f5843ced543bae")   # (the game's AddInt node in BottomOfTheWell Room 10)


def make_int_module(name, flag, kind="add", value=1, guid=None):
    """The template module with its trigger turned into an Int trigger: kind "add" (TriggerGameDataAddInt, AddValue)
    or "set" (TriggerGameDataSetIntSum, SetValue), as the game's own nodes are laid out. guid: the node's 16-byte GUID
    (default: the template trigger's)."""
    import ainbedit
    from ainb import Ainb
    node_name, hash_, vname = (("TriggerGameDataAddInt", ADDINT_HASH, "AddValue") if kind == "add" else
                               ("TriggerGameDataSetIntSum", SETINTSUM_HASH, "SetValue"))
    d = make_module(name, "X")[:-2]
    L = ainbedit.Layout(d)
    strings = {}
    for s in (flag, node_name, vname, "GameDataNameInt"):
        strings[s] = len(d) - L.pool
        d += s.encode() + b"\0"
    L = ainbedit.Layout(d)
    e, b = L.node(1), L.body(1)
    assert L.string(ainbedit.u32(d, e + 8)) == "TriggerGameDataSetBoolTrue"
    assert struct.unpack_from("<4I", d, b + 0x30) == (0, 1, 0, 0) and (L.io_tables[1] - L.io_tables[0]) // 16 == 1
    out = bytearray(d)
    struct.pack_into("<I", out, e + 8, strings[node_name])
    struct.pack_into("<I", out, e + 0xC, hash_)
    if guid:
        out[e + 0x2C:e + 0x3C] = guid
    struct.pack_into("<I", out, L.io_tables[6], strings["GameDataNameInt"])
    struct.pack_into("<I", out, L.io_tables[6] + 12, strings[flag])
    struct.pack_into("<I", out, b + 0x34, 2)
    d = bytes(out)
    entry = bytearray(d[L.io_tables[0]:L.io_tables[0] + 16])
    struct.pack_into("<I", entry, 0, strings[vname])
    struct.pack_into("<i", entry, 12, value)
    if kind == "add":   # first, before GameDataArrayIndex (insert moves the table's start: put it back)
        pos = L.io_tables[0]
        d = bytearray(ainbedit.insert(d, pos, bytes(entry)))
        struct.pack_into("<I", d, ainbedit.Layout(bytes(d)).io, pos)
        d = bytes(d)
        want = [(vname, value), ("GameDataArrayIndex", -1)]
    else:   # after it, flags as the game's SetValue
        struct.pack_into("<I", entry, 8, 0x800000)
        d = ainbedit.insert(d, L.io_tables[1], bytes(entry))
        want = [("GameDataArrayIndex", -1), (vname, value)]
    n = Ainb(d).node(1)
    got = [(x["name"], x["value"] if x["type"] != "bool" else tuple(x["src"])) for x in n["inputs"]]
    assert n["name"] == node_name and got == want + [("IsDrive", (0, 3)), ("GameDataNameInt", flag)], got
    return d


def make_key_module(name, flag):
    """The template module renamed to name, adding 1 to the Int flag when the chest opens."""
    return make_int_module(name, flag, "add", 1)


def _old_make_key_module(name, flag):
    import ainbedit
    from ainb import Ainb
    d = make_module(name, "X")   # (renamed, flag string cut: rebuilt below)
    d = d[:-2]   # (drop the placeholder "X\0": the pool ends where it was)
    L = ainbedit.Layout(d)
    pool_len = len(d) - L.pool
    strings = {}
    for s in (flag, "TriggerGameDataAddInt", "AddValue", "GameDataNameInt"):
        strings[s] = len(d) - L.pool
        d += s.encode() + b"\0"
    L = ainbedit.Layout(d)
    e = L.node(1)
    assert L.string(ainbedit.u32(d, e + 8)) == "TriggerGameDataSetBoolTrue"
    b = L.body(1)
    assert struct.unpack_from("<4I", d, b + 0x30) == (0, 1, 0, 0), "template trigger's int inputs changed"
    assert struct.unpack_from("<2I", d, b + 0x30 + 16 * 3) == (0, 1), "template trigger's string input changed"
    n_int = (L.io_tables[1] - L.io_tables[0]) // 16
    assert n_int == 1, "template: other int inputs"
    out = bytearray(d)
    struct.pack_into("<I", out, e + 8, strings["TriggerGameDataAddInt"])
    struct.pack_into("<I", out, e + 0xC, ADDINT_HASH)
    # (+0x24 is the node's first entry in the file's precondition list, +0x26 their count: the game's AddInt node
    # has 1 there because its list holds another node first. Copying that 1 pointed past this file's one-entry list,
    # and the trigger never ran (seed 1220: SmallKeyNum stayed 0). The template's own 0 stays.)
    s_entry = L.io_tables[6]   # its string input: name and value
    struct.pack_into("<I", out, s_entry, strings["GameDataNameInt"])
    struct.pack_into("<I", out, s_entry + 12, strings[flag])
    struct.pack_into("<I", out, b + 0x34, 2)   # int inputs: 2
    d = bytes(out)
    add_value = bytearray(d[L.io_tables[0]:L.io_tables[0] + 16])   # (a copy of GameDataArrayIndex's entry)
    struct.pack_into("<I", add_value, 0, strings["AddValue"])
    struct.pack_into("<i", add_value, 12, 1)
    pos = L.io_tables[0]
    d = bytearray(ainbedit.insert(d, pos, bytes(add_value)))   # AddValue first, as the game's own
    struct.pack_into("<I", d, ainbedit.Layout(bytes(d)).io, pos)   # (the int table still starts here: insert moved it)
    d = bytes(d)
    a = Ainb(d)
    n = a.node(1)
    assert n["name"] == "TriggerGameDataAddInt", n["name"]
    got = [(x["name"], x["value"] if x["type"] != "bool" else tuple(x["src"])) for x in n["inputs"]]
    assert got == [("AddValue", 1), ("GameDataArrayIndex", -1), ("IsDrive", (0, 3)), ("GameDataNameInt", flag)], got
    assert a.node(0)["name"] == "ActorLogicTreasureBox"
    return d


SMALL_KEY = "Item_Dungeon_SmallKey"
CREDITED = tuple(FLAGS) + (SMALL_KEY,)


def credit_flag(item, dungeon):
    return key_flag(dungeon) if item == SMALL_KEY else flag_for(item, dungeon)


def credit_module(name, item, dungeon):
    """The module crediting dungeon for item (a key adds 1 to its count; the others set their Bool)."""
    flag = credit_flag(item, dungeon)
    return make_key_module(name, flag) if item == SMALL_KEY else make_module(name, flag)


def add_groups(data, chests):
    """Map data with an AiGroup per chest Hash -> module name of chests (a rewrite, bymlw)."""
    import bymlw
    root, meta = bymlw.loads(data)
    groups = root.setdefault("AiGroups", [])
    used = {int(g["Hash"]) for g in groups}
    for h, name in sorted(chests.items()):
        gh = int(hashlib.sha1(f"aigroup/{h}".encode()).hexdigest()[:16], 16)
        assert gh not in used
        groups.append({"Hash": bymlw.U64(gh), "Logic": module_path(name)[1],
                       "References": [{"Id": INSTANCE, "Path": INSTANCE, "Reference": bymlw.U64(h)}]})
    return bymlw.dumps(root, meta["endian"], meta["version"])
