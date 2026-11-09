"""Crediting a dungeon for its small key, map, compass or boss key found outside it, by a chest event that also has
Navi name the dungeon (and, for keys, the dungeon's key count, as the game shows the Gold Skulltula count).

Why an event: these items are one actor each for every dungeon, credited by the compiled pickup code to the dungeon
the player is in (nothing in a field area). The record is game data: DungeonInfo.<Dungeon>.SmallKeyNum (Int),
IsGetMap / IsGetCompass / IsGetBossKey (Bool). Map logic could set the Bools (dungeoncredit.py, worked in game, seed
1218) but its Int triggers never ran (seeds 1220-1224); a chest event adds keys (seed 1225: 2 keys).

The game's own key-adding event: MiniGame_SequentialChoiceTreasure (the Market's treasure chest game), entry
MiniGame_SequentialChoiceTreasure_OpenHitTreasureBox (the GetEventName of its hit chests):
  59 EventQueryExistEventStarter -> 61 subflow OpenTreasureBox (camera; 94 SetGameDataBool
  MiniGame_ChoiceTreasure_OpenedEitherBox; Dm_SY_TreasureboxSmall::SuccessForMiniGame: the box opens, gives its
  content, "You found a small key!") -> 62 subflow GetKey (33 AddOrSubGameDataInt MiniGame_SmallKeyNum +1, the key
  count-up UI) -> 69 subflow HitCountInclement. Event 109 is Npc_Navi.EventTalk (a RestrictedDoor message).

Per kind and dungeon, a copy of that flow (flowchart name RandoKey_<Dungeon> etc., padded to the same length), and an
event row (RSDB EventSetting and GameAnalyzedEventInfo, copies of MiniGame_ChoiceTreasure_OpenHitTreasureBox's):
  - key: 33 -> DungeonInfo.<D>.SmallKeyNum; 94 -> UI_RequestSmallKeyCountUpDemo (set right after anyway);
    62 -> 109 (Navi) -> end.
  - map / compass / boss key: 61 -> 109 -> end; only the message: their flag is set by map logic
    (dungeoncredit.py), since 94 runs on one camera path only (seed 1226: Navi spoke, the map flag stayed off).
  - 109's MessageId -> EventFlowMsg/RestrictedDoor:Rando_<Kind>_<D>, a message added to that file in every language
    (English text in all of them for now).
BFEVFL v4 layout used: header +0x18 RELT offset (u32), +0x1C file size; "STR " section, +0x10 string count, strings
(u16 length, bytes, NUL, padded to 2), pointed at by their length; RELT: magic, self offset u32, section count;
section 0 (+0x10): pointer u64, offset u32, size u32 (+0x1C), first entry, entry count. New strings go at the end of
the pool; they hold no pointers, so only the offsets after them move.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from nxfmt import ROMFS, unzs

SRC_FLOW = "Event/EventFlow/MiniGame_SequentialChoiceTreasure.bfevfl.zs"
SRC_NAME = "MiniGame_SequentialChoiceTreasure"
SRC_EVENT = "MiniGame_ChoiceTreasure_OpenHitTreasureBox"   # (the RSDB rows copied)
EVENT_SETTING = "RSDB/EventSetting.Product.111.rstbl.byml.zs"
EVENT_INFO = "RSDB/GameAnalyzedEventInfo.Product.111.rstbl.byml.zs"
MSG_FILE = "EventFlowMsg/RestrictedDoor.msbt"
ADD_EVENT, FLAG_EVENT, OPEN_CALL, KEY_CALL, NAVI_EVENT = 33, 94, 61, 62, 109
SIZE_MARGIN = 2048

KINDS = {"Item_Dungeon_SmallKey": "Key", "Item_Dungeon_Map": "Map", "Item_Dungeon_Compass": "Compass",
         "Item_Dungeon_BossKey": "BossKey"}
FLAG = {"Map": "IsGetMap", "Compass": "IsGetCompass", "BossKey": "IsGetBossKey"}
NAMES = {"InsideTheDekuTree": "the Deku Tree", "DodongoCavern": "Dodongo's Cavern",
         "InsideJabuJabuBelly": "Jabu-Jabu's Belly", "ForestTemple": "the Forest Temple", "FireTemple": "the Fire Temple",
         "WaterTemple": "the Water Temple", "ShadowTemple": "the Shadow Temple", "SpiritTemple": "the Spirit Temple",
         "BottomOfTheWell": "the Bottom of the Well", "IceCavern": "the Ice Cavern",
         "GerudoTrainingGround": "the Gerudo Training Ground", "InsideGanonCastle": "Ganon's Castle",
         "GanonCastle": "Ganon's Tower"}


def flow_name(kind, dungeon):
    n = f"Rando{kind}_{dungeon}"
    assert len(n) <= len(SRC_NAME), n
    return n + "_" * (len(SRC_NAME) - len(n))


def event_name(kind, dungeon):
    return f"RandoCredit_{kind}_{dungeon}"


def flow_path(kind, dungeon):
    return f"Event/EventFlow/{flow_name(kind, dungeon)}.bfevfl.zs"


def message_label(kind, dungeon):
    return f"Rando_{kind}_{dungeon}"


def message(kind, dungeon):
    import msbt
    name = NAMES.get(dungeon, dungeon)
    if kind == "Key":
        return (f"A small key for {name}!\nKeys for {name}: ".encode() +
                msbt.gamedata_int_tag(f"DungeonInfo.{dungeon}.SmallKeyNum"))
    what = {"Map": "map", "Compass": "compass", "BossKey": "boss key"}[kind]
    return f"This is the {what} of {name}!".encode()


def _param_ptr(f, ev, key):
    """Position of the u64 pointer holding the string value of an event's param key."""
    ptr = f.event(ev)["params"]
    n = struct.unpack_from("<H", f.d, ptr + 2)[0]
    dic = struct.unpack_from("<Q", f.d, ptr + 8)[0]
    for k in range(n):
        name = f.s(struct.unpack_from("<Q", f.d, dic + 8 + 0x10 * (k + 1) + 8)[0])
        if name == key:
            v = struct.unpack_from("<Q", f.d, ptr + 0x10 + 8 * k)[0]
            return v + 0x10
    raise KeyError(key)


def _add_strings(d, strings):
    """d with strings appended to the string pool (before RELT); returns (data, {string: pointer value})."""
    relt = struct.unpack_from("<I", d, 0x18)[0]
    assert d[relt:relt + 4] == b"RELT" and struct.unpack_from("<I", d, relt + 4)[0] == relt
    stro = d.find(b"STR ")
    assert 0 < stro < relt
    blob, where = bytearray(), {}
    for s in strings:
        where[s] = relt + len(blob)
        e = struct.pack("<H", len(s.encode())) + s.encode() + b"\0"
        blob += e + b"\0" * (len(e) % 2)
    blob += b"\0" * (-len(blob) % 8)
    n = len(blob)
    out = bytearray(d[:relt]) + blob + bytearray(d[relt:])
    struct.pack_into("<I", out, 0x18, relt + n)
    struct.pack_into("<I", out, 0x1C, struct.unpack_from("<I", d, 0x1C)[0] + n)
    r = relt + n
    struct.pack_into("<I", out, r + 4, r)
    assert struct.unpack_from("<I", out, r + 8)[0] == 1, "RELT: more than one section"
    struct.pack_into("<I", out, r + 0x1C, struct.unpack_from("<I", out, r + 0x1C)[0] + n)
    struct.pack_into("<I", out, stro + 0x10, struct.unpack_from("<I", out, stro + 0x10)[0] + len(strings))
    return bytes(out), where


def make_flow(kind, dungeon):
    from evfl import Flow, END
    d = unzs((ROMFS / SRC_FLOW).read_bytes())
    f = Flow(d)
    acts = f.actors()
    pr = lambda ev: f.params(f.event(ev)["params"])
    assert pr(ADD_EVENT).get("GameDataName") == "MiniGame_SmallKeyNum" and pr(ADD_EVENT).get("Value") == 1, "event 33"
    assert pr(FLAG_EVENT).get("GameDataName") == "MiniGame_ChoiceTreasure_OpenedEitherBox", "event 94"
    e61, e62, e109 = f.event(OPEN_CALL), f.event(KEY_CALL), f.event(NAVI_EVENT)
    assert e61["type"] == "subflow" and e61["entry"] == "MiniGame_SequentialChoiceTreasure_OpenTreasureBox" and \
        e61["next"] == KEY_CALL, "event 61"
    assert e62["type"] == "subflow" and e62["entry"] == "MiniGame_SequentialChoiceTreasure_GetKey", "event 62"
    assert acts[e109["actor"]]["name"] == "Npc_Navi" and e109["next"] == END and \
        pr(NAVI_EVENT)["MessageId"].startswith("EventFlowMsg/RestrictedDoor:"), "event 109"
    p33, p94, p109 = (_param_ptr(f, ev, k) for ev, k in ((ADD_EVENT, "GameDataName"), (FLAG_EVENT, "GameDataName"),
                                                        (NAVI_EVENT, "MessageId")))
    old = struct.unpack_from("<Q", d, p33)[0]
    assert d[old:old + 2] == struct.pack("<H", len("MiniGame_SmallKeyNum")), "string pointers don't point at the length"
    msg_id = f"{MSG_FILE[:-5]}:{message_label(kind, dungeon)}"
    if kind == "Key":
        add, flag = f"DungeonInfo.{dungeon}.SmallKeyNum", "UI_RequestSmallKeyCountUpDemo"
        strings = [add, flag, msg_id]
    else:   # (credited by map logic, dungeoncredit.py: event 94 runs on one of OpenTreasureBox's two camera paths only,
        # so pointed at IsGetMap it left the flag off in seed 1226; it keeps the game's minigame flag)
        add, flag = None, "MiniGame_ChoiceTreasure_OpenedEitherBox"
        strings = [msg_id]
    d, where = _add_strings(d, strings)
    out = bytearray(d)
    if add:
        struct.pack_into("<Q", out, p33, where[add])
        struct.pack_into("<Q", out, p94, where[flag])
    struct.pack_into("<Q", out, p109, where[msg_id])
    if kind == "Key":
        struct.pack_into("<H", out, e62["next_field"], NAVI_EVENT)
    else:
        struct.pack_into("<H", out, e61["next_field"], NAVI_EVENT)
    old_name = struct.pack("<H", len(SRC_NAME)) + SRC_NAME.encode() + b"\0"
    new_name = struct.pack("<H", len(SRC_NAME)) + flow_name(kind, dungeon).encode() + b"\0"
    assert out.count(old_name) == 3, "key flow changed: its name strings"
    out = out.replace(old_name, new_name)
    g = Flow(bytes(out))
    gp = lambda ev: g.params(g.event(ev)["params"])
    assert gp(FLAG_EVENT)["GameDataName"] == flag and gp(NAVI_EVENT)["MessageId"] == msg_id
    assert g.event(KEY_CALL if kind == "Key" else OPEN_CALL)["next"] == NAVI_EVENT and g.name == flow_name(kind, dungeon)
    if add:
        assert gp(ADD_EVENT)["GameDataName"] == add
    return bytes(out)


def add_rows(table, data, credits):
    """EventSetting / GameAnalyzedEventInfo data with a row per (kind, dungeon) of credits, copied from SRC_EVENT's."""
    import copy
    import bymlw
    root, meta = bymlw.loads(data)
    rows = {r["__RowId"]: r for r in root}
    new = []
    for kind, dn in sorted(credits):
        r = copy.deepcopy(rows[SRC_EVENT])
        r["__RowId"] = event_name(kind, dn)
        if table == EVENT_SETTING:
            assert r["FlowchartFileName"] == f"Work/Event/EventFlow/{SRC_NAME}.evfc", r["FlowchartFileName"]
            r["FlowchartFileName"] = f"Work/Event/EventFlow/{flow_name(kind, dn)}.evfc"
        assert r["__RowId"] not in rows
        new.append(r)
    root = sorted(root + new, key=lambda r: r["__RowId"].encode())
    return bymlw.dumps(root, meta["endian"], meta["version"])


def add_messages(sarc_data, credits):
    """A Mals language archive with the credit messages added to MSG_FILE."""
    import bymlw
    import msbt
    from nxfmt import parse_sarc
    files = parse_sarc(sarc_data)
    files[MSG_FILE] = msbt.add_messages(files[MSG_FILE], {message_label(k, d): message(k, d) for k, d in credits})
    return bymlw.build_sarc(files, data_align=bymlw.sarc_alignments(sarc_data))


def mals_files():
    return sorted(p.relative_to(ROMFS).as_posix() for p in (ROMFS / "Mals").glob("*.sarc.zs"))
