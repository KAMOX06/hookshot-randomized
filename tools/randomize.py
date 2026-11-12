"""Chest randomizer, no logic: shuffles what the game's chests hold and writes the result as an overlay folder, which
hookshot.exe reads in place of the game's own files when HOS_FS_OVERLAY points at it. The game's files are never
changed.

- A chest is one Hash; its child and adult copies (and any other map file it is in) get the same item.
- Dungeon items (small keys, boss keys, maps, compasses) move only between chests of their own dungeon.
- Everything else is shuffled between all the remaining chests, anywhere.
- Left alone: chests that can be reopened (the Market treasure game, the DreamWorld boss rush), chests that give a
  substitute when the item is already owned (Drop__CheckActor), and everything under DreamWorld.
- GetEventName runs after the chest opens. Most are tied to the chest's place (they warp the player around it, or
  carry story: Ruto, Nabooru, the Serenade of Water), and stay. The item ones (..._Get..., such as
  Get_StoneOfForest_GetSword, which plays Dm_Kokiri_0063 and hands the player a Kokiri Sword of its own) are taken
  off a chest whose item changes, which then shows its new item with the generic chest demo, Dm_SY_Treasurebox.
- Some seeds can be unbeatable: nothing checks that an item is reachable before it is needed.

Open settings change the default value of story flags in GameData/GameDataList, which a new save starts from:
- open forest: KokiriForest_Npc_Kokiri_007_Child_Blocker_00 off. While it is on, an invisible wall
  (FldObj_AirWall_Player_GenerateNavMesh, Presence on that flag) stands at the Kokiri guarding the forest exit.
- open door: Market_Child_TimeTempleDoor_Opened on, and the door logic rewired to read it. In
  Logic/Market_Common_f252 the door's isOpen comes from an OR node (5) of three sources (multi params 0-2): the
  opening event's EventBool, DoorOfTime challenge IsAfter Step06, and IsEqual Step06. The flag's own node (6,
  HoldGameDataGetBoolSimple) fed nothing, so the flag alone left the door shut. Source 1 now reads node 6, so the
  door opens on the event, on Step06, or on the flag (the game sets the flag and Step06 together, so normal play
  is unchanged). The quest itself is left at NotReady: moving it on would mark Impa, Zelda's Lullaby and the
  Ocarina of Time as done.
  The Master Sword's first pull has the same kind of check: in Logic/Market_Child_Prefab_e700 the event
  Market_Child_DoorOfTime_Draw fires on AND(DoorOfTime IsEqual Step06, the sword's ReceiveDoAction). Its first
  source now reads DoorOfTime IsBefore Step06 (same node 2), which holds until the first draw: its Rauru scene
  (DoorOfTime::SageRoom_MeetRauru) moves the quest to Complete. After that the game's own ChildToAdult path
  (AllSage IsAfter Ready) runs. (AllSage is no help for the first draw: a save reaches AllSage Ready early on,
  so "AllSage IsBefore Ready" was already false in a test save.)

Intro skip (OpeningEvent, two u16 links changed in place):
- entry point Opening_LoadingKokiriForest starts at event 14 (the "forest loaded?" check) instead of event 7, which
  plays the Dm_Kokiri_0000 movie while the forest loads.
- event 23 goes to event 10 (Navi placed, fade in, music, autosave) instead of event 0, which plays Dm_Kokiri_0030
  (Navi waking Link) and fades out.

Cutscene skips: see cutscenes.py (Saria at Link's house, Saria's talk on the bridge).

Bombs (on unless --bombs-need-bracelet): in this remake a held bomb has the Bomb Flowers' LiftGrade, so without the
Goron's Bracelet it vanishes the frame it's taken out. The vanilla rule is kept as an option: a future logic must then
treat the Bomb Bag as usable only with the bracelet. Planned as a GUI toggle.

Magic (on unless --free-magic): the Lens of Truth, the spells and the magic arrows no longer give the magic meter;
only the Great Fairies do (itempatches.py). A future logic must then treat those items as usable only after a Great
Fairy.

usage: randomize.py [--seed N] [--out <overlay dir>] [--closed-forest] [--closed-door] [--keep-intro]
                    [--keep-cutscenes] [--bombs-need-bracelet] [--free-magic] [--no-deku-nut] [--place HASH=ITEM]
                    [--keep ITEM]   (e.g. --keep Item_Sword_Kokiri: that chest isn't shuffled)

Start with Deku Nuts x0 (on unless --no-deku-nut): pushed by the game's new-game setup (see GAME_START), so an adult
item the child finds first can't become the selected item and block every item.
"""
import argparse
import collections
import json
import random
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from nxfmt import ROMFS
from bymledit import read_original, write_zs, apply_items, set_bool_defaults, set_int_defaults, raise_sizes, \
    set_gyaml, add_sizes
from gamedata import GDL, murmur3

HERE = Path(__file__).parent
DUNGEONS = {"InsideTheDekuTree", "DodongoCavern", "InsideJabuJabuBelly", "ForestTemple", "FireTemple", "WaterTemple",
            "ShadowTemple", "SpiritTemple", "BottomOfTheWell", "IceCavern", "GerudoTrainingGround",
            "InsideGanonCastle", "GanonCastle"}
EXCLUDED_AREAS = {"DreamWorld"}
# Field areas with a dungeon record of their own (RSDB DungeonParam TargetGameData): a dungeon item picked up there
# would count for it, so the credited dungeon items aren't placed there.
NO_CREDIT_AREAS = {"GerudoFortress", "HyruleCastle"}
# (small keys by a chest event, keycredit.py: the map logic's Int triggers never ran; the others by map logic)
DUNGEON_CREDITED = ("Item_Dungeon_Map", "Item_Dungeon_Compass", "Item_Dungeon_BossKey", "Item_Dungeon_SmallKey")
STORY_EVENTS = {"SageOfWater_MelodyGet_Water"}   # "Get" in the name, but it teaches a song: keep it


# Chest events a small key's credit event may replace: only cosmetic (the Kokiri Forest chests' scene without Navi's
# chatter). Story events (Ruto, Nabooru, Dampe's race, the songs) keep their chest free of keys.
REPLACEABLE_EVENTS = {"KokiriForest_TreasureBox_NaviChatOff"}


def has_story_event(loc):
    return bool(loc["event"]) and not is_item_event(loc["event"]) and loc["event"] not in REPLACEABLE_EVENTS


def is_item_event(name):
    """A GetEventName that shows or hands out the chest's original item (see the module notes)."""
    return bool(name) and "_Get" in name and name not in STORY_EVENTS


OPEN_FOREST = {"KokiriForest_Npc_Kokiri_007_Child_Blocker_00": False}
OPEN_DOOR = {"Market_Child_TimeTempleDoor_Opened": True}
# Start owning the Deku Nut item with 0 nuts. The R item wheel (hold R) opens only with two items usable at Link's
# age; an item the child can't use (e.g. shuffled Ice Arrows) taken first is selected and the wheel stays shut until a
# second child item comes. The pouch keeps one StockNum per item (PouchData.Item_DekuNut), ownership is IsGet.
# Capacity must come too: the game gives it (Item_NutBag_Small, 20) only with the first nuts of a save that doesn't
# own them yet, so with ownership alone bought nuts were lost (tested 2026-11-08). Start at 20, as after the first nuts.
START_DEKU_NUT = {"IsGet.Item_DekuNut": True, "PouchContentMaxNum.DekuNut": 20}   # old way, broken; kept for --deku-nut-flags
# The way that works with the game: its new-game setup (Sequence/SetPouchAndStatusForGameStart, which pushes the
# wallet and tunics with SeqOneShotPushToPouch) gets one more push: Deku Nuts x0 (ainbedit.py). The first item a save
# receives becomes the selected one; with the nuts first, an adult item found first by the child (Ice Arrows) stays out
# of the item bar, as it does when found second. Found 2026-11-08: Ice Arrows selected first block every item.
GAME_START = "Sequence/SetPouchAndStatusForGameStart.module.ainb"   # not compressed
# The bag first: the game gives the nut capacity (Item_NutBag_Small, 20) only with the first nuts of a save that owns
# none, so with the nuts already owned, bought nuts were lost (tested on seed 1115). It's a key item like the starting
# wallet (Item_Wallet_Small, also pushed here), so the nuts are still the first item that can go on the item bar.
START_PUSHES = [("Work/Actor/Item_NutBag_Small.engine__actor__ActorParam.gyml", 1),
                ("Work/Actor/Item_DekuNut.engine__actor__ActorParam.gyml", 0)]
RSTB = next((ROMFS / "System" / "Resource").glob("ResourceSizeTable.Product.*.rsizetable.zs"))
OPENING = "Event/EventFlow/OpeningEvent.bfevfl.zs"
DOOR_LOGIC = "Logic/Market_Common_f252.logic.module.ainb"   # not compressed


def wire_door_to_flag(data):
    """The door logic with its OR node's source 1 (IsAfter Step06) replaced by the door flag (see the module notes)."""
    import struct
    from ainb import Ainb
    a = Ainb(data)
    out = bytearray(data)
    multi = struct.unpack_from("<I", data, 0x38)[0]
    flag_node, check_node, or_node, door_node = a.node(6), a.node(8), a.node(5), a.node(3)
    assert flag_node["name"] == "HoldGameDataGetBoolSimple" and \
        flag_node["inputs"][1]["value"] == "Market_Child_TimeTempleDoor_Opened", "door logic changed: flag node"
    assert check_node["name"] == "HoldChallengeCurrentStepCheckOrder" and \
        check_node["outputs"]["bool"] == ["IsAfter", "IsBefore", "IsEqual"], "door logic changed: step check"
    assert or_node["name"] == "OperateORFixInputTrue" and or_node["inputs"][0]["src"] == (-100, 3), "door logic changed: OR"
    assert door_node["name"] == "ActorLogicGateDoor", "door logic changed: door node"
    assert struct.unpack_from("<hh", data, multi + 8) == (8, 0), "door logic changed: OR sources"
    struct.pack_into("<hh", out, multi + 8, 6, 0)
    print("  door: OR source 1 now reads Market_Child_TimeTempleDoor_Opened")
    return bytes(out)


SWORD_LOGIC = "Logic/Market_Child_Prefab_e700.logic.module.ainb"   # not compressed


def first_draw_before_allsage(data):
    """The Master Sword logic with the first pull's DoorOfTime Step06 check replaced (see the module notes)."""
    import struct
    from ainb import Ainb
    a = Ainb(data)
    out = bytearray(data)
    multi = struct.unpack_from("<I", data, 0x38)[0]
    door_step, allsage = a.node(2), a.node(5)
    assert door_step["inputs"][1]["value"] == "Step06" and "DoorOfTime" in door_step["inputs"][0]["value"], "sword logic changed: Step06 check"
    assert allsage["inputs"][1]["value"] == "Ready" and "AllSage" in allsage["inputs"][0]["value"], "sword logic changed: AllSage check"
    assert allsage["outputs"]["bool"] == ["IsAfter", "IsBefore", "IsEqual"], "sword logic changed: AllSage outputs"
    assert a.node(1)["name"] == "OperateAND" and a.node(1)["inputs"][0]["src"] == (-100, 2), "sword logic changed: AND"
    assert door_step["outputs"]["bool"] == ["IsAfter", "IsBefore", "IsEqual"], "sword logic changed: Step06 outputs"
    assert struct.unpack_from("<hh", data, multi) == (2, 2), "sword logic changed: AND sources"
    struct.pack_into("<hh", out, multi, 2, 1)
    print("  sword: first pull now needs DoorOfTime before Step06 instead of at Step06")
    return bytes(out)


def skip_intro(data):
    """OpeningEvent data with its two cutscenes bypassed (see the module notes)."""
    import struct
    from evfl import Flow
    f = Flow(data)
    out = bytearray(data)
    first, pos = f.entry_points()["Opening_LoadingKokiriForest"]
    ev7, ev14, ev23 = f.event(7), f.event(14), f.event(23)
    assert first == 7 and "Dm_Kokiri_0000" in f.param_strings(ev7["params"]), "OpeningEvent changed: loading entry"
    assert ev14["type"] == "switch" and ev14["cases"][0][:2] == (1, 12), "OpeningEvent changed: load check"
    assert ev23["next"] == 0 and "Dm_Kokiri_0030" in f.param_strings(f.event(0)["params"]), "OpeningEvent changed: intro"
    assert f.event(10)["name"] == "Event2" and f.event(10)["next"] == 26, "OpeningEvent changed: Navi warp"
    struct.pack_into("<H", out, pos, 14)
    struct.pack_into("<H", out, ev23["next_field"], 10)
    print("  intro: Dm_Kokiri_0000 movie and Dm_Kokiri_0030 skipped")
    return bytes(out)


# Bombs give the Bomb Bag (option). In this remake the first bombs found bring the Bomb Bag, and no data edit stopped
# it (2026-11-09, seeds 1210-1213, each tried in game): not the chest bombs' pack (IsAbleToGetBagIfGet false,
# SmallBagActor ""), not SmallBagActor taken off the PouchActorInfo rows of Item_Bomb / Obj_Bomb / Pouch_Bomb, not off
# the bombs' PouchContentParam in all four packs carrying a copy. The rule is in the compiled code. So with the option
# off, a bomb pack placed in a chest becomes a Bombchu pack of the same size (chest_actor): only the Bomb Bag itself
# gives bombs, and bombs in shops wait for the bag (the shop's pouch check refuses them before). On: the game as it is.
BOMBS_AS_BOMBCHUS = {"Item_BombBundle_Small": "Item_BombchuBundle_Small"}   # 5 bombs -> 5 Bombchus
# Once the bag is owned such a chest gives the bombs after all, by the chests' own owned-item check: a chest with
# Drop__CheckActor gives Drop__DropActor_Substitute instead when that actor is owned (the game has two chests giving a
# red rupee, or arrows when the Bow is owned). Map files get the two keys by a rewrite (bymlw): most don't have the key
# names yet.
BOMB_CHECK = {"Drop__CheckActor": "Item_BombBag_Small", "Drop__DropActor_Substitute": "Item_BombBundle_Small"}
# Only one chest kind reads those keys: every chest of the game that has them is an Obj_TBox_A_06 (its own AI, DropParam
# and Drop__CheckCondition). It's the plain small chest (Obj_TBox_A_01, same model, its $parent) with that check.
# A plain Obj_TBox_A_01 given the keys still gave Bombchus with the bag owned (seed 1215). So the chest becomes an
# A_06; other chest kinds (big chests, ...) keep giving Bombchus.
CHECK_CHEST = {"Obj_TBox_A_01": "Obj_TBox_A_06", "Obj_TBox_A_01_NoBake": "Obj_TBox_A_06_NoBake"}


def needs_bag_check(item, bombs_give_bag=True, chest="Obj_TBox_A_01"):
    """Whether a chest (of actor kind chest) holding item gives its bombs only with the bag (BOMB_CHECK)."""
    import shops
    return not bombs_give_bag and shops.chest_form(item) in BOMBS_AS_BOMBCHUS and chest in CHECK_CHEST


def set_chest_checks(data, checks):
    """Map data with each chest Hash -> {key: value} of checks added to its Dynamic (a rewrite, bymlw)."""
    import bymlw
    root, meta = bymlw.loads(data)
    left = dict(checks)
    for a in root["Actors"]:
        h = int(a.get("Hash", -1))
        if h in left:
            a.setdefault("Dynamic", {}).update(left.pop(h))
    assert not left, f"chests not found: {sorted(left)}"
    return bymlw.dumps(root, meta["endian"], meta["version"])


def chest_actor(item, bombs_give_bag=True):
    """What a chest holding item gives: its chest form (shops.chest_form), with bombs as Bombchus when bombs don't
    bring the bag."""
    import shops
    a = shops.chest_form(item)
    return a if bombs_give_bag else BOMBS_AS_BOMBCHUS.get(a, a)

# Bombchus without the Bomb Bag (option): in this remake Bombchus have no capacity of their own, they use the bombs'
# (PouchActorInfo Item_Bombchu: MaxNumGameData "Bomb"; the save has no PouchContentMaxNum.Bombchu). Without a Bomb Bag
# that capacity is 0, so Bombchus found are lost (tested 2026-11-09, seed 1202); the bag sets it to 20 and fills both.
# The option starts a save at 10. The bag itself isn't given: shops still won't sell bombs and bombs don't drop.
# Not 20 (the small bag's): the bag fills bombs (and owned Bombchus) only when it raises the capacity, so with 20
# already a bought bag gave no bombs (seed 1204, 2026-11-09); from 10 it still raises it to 20.
# Limit of one shared counter: bombs found in a chest before the bag can be held too.
BOMBCHUS_WITHOUT_BAG = {"PouchContentMaxNum.Bomb": 10}
BOMB_PACK = "Pack/Actor/Item_Bomb.pack.zs"
BOMB_LIFT = "Component/LiftableParam/Item_Bomb.game__component__LiftableParam.bgyml"
LIFT_GRADE_BOMBFLOWER = 3655598229   # Item_Bomb, Obj_BombFlower_*: needs the Goron's Bracelet
LIFT_GRADE_POT = 3069107721          # pots, grass, Cuccos, Item_Bombchu: anyone can lift


def bombs_without_bracelet(data):
    """Item_Bomb.pack with the bomb's LiftGrade lowered from the Bomb Flowers' grade to the pots' one.

    In this remake a bomb taken out of the bag is a liftable object with the Bomb Flowers' LiftGrade, so without the
    Goron's Bracelet Link can't hold it: it shows for a frame and is gone. The normal game always gives the bracelet
    before the Bomb Bag; a shuffled Bomb Bag can come first. One u32 changes in place, so nothing moves or grows.
    """
    import struct
    from nxfmt import Byml, parse_sarc
    from bymledit import entries
    lift = parse_sarc(data)[BOMB_LIFT]
    start = data.find(lift)
    assert start >= 0 and data.find(lift, start + 1) < 0, "Item_Bomb.pack changed: LiftableParam not unique"
    b = Byml(lift)
    root = struct.unpack_from(b.e + "I", lift, 12)[0]
    field = next(pos for key, t, pos in entries(b, root) if key == "LiftGrade")
    assert b.u32(field) == LIFT_GRADE_BOMBFLOWER, "Item_Bomb.pack changed: LiftGrade"
    out = bytearray(data)
    struct.pack_into(b.e + "I", out, start + field, LIFT_GRADE_POT)
    assert Byml(parse_sarc(bytes(out))[BOMB_LIFT]).root["LiftGrade"] == LIFT_GRADE_POT
    print("  bombs: liftable without the Goron's Bracelet (LiftGrade as pots and Bombchus)")
    return bytes(out)


_gd_hashes = {}


def gd_hashes(kind):
    """The hashes of the game's GameDataList entries of one kind (Bool, BoolExp, ...)."""
    if kind not in _gd_hashes:
        from nxfmt import load_byml
        data = load_byml(GDL)["Data"]
        for k, entries in data.items():
            _gd_hashes[k] = {int(e["Hash"]) for e in entries if isinstance(e, dict) and "Hash" in e}
    return _gd_hashes.get(kind, set())


def load_chests():
    if not (HERE / "chests.json").exists():   # made from the game's files, so not shipped: build it on first use
        import subprocess
        subprocess.run([sys.executable, "-I", str(HERE / "chests.py")], check=True, stdout=subprocess.DEVNULL)
    chests = json.loads((HERE / "chests.json").read_text(encoding="utf-8"))
    by_hash = {}
    for c in chests:
        if not c["drop"] or c["repeat"] or c["check"]:
            continue
        area = c["file"].split("\\")[0]
        if area in EXCLUDED_AREAS:
            continue
        loc = by_hash.setdefault(c["hash"], {"hash": c["hash"], "area": area, "item": c["drop"], "files": [],
                                             "actor": c["actor"], "event": c["event"]})
        loc["files"].append("Banc/" + c["file"].replace("\\", "/"))
    return list(by_hash.values())


def size_table_base():
    """crc32 of the size table a seed's table is made from (nxfmt.rstb_path): when Hookshot's copy changes (an
    update), seeds made before it would crash the game; the launcher compares this before starting one."""
    import binascii
    from nxfmt import rstb_path
    return binascii.crc32(rstb_path()[1].read_bytes())


def shuffle(locs, rng, slots=None, dungeon_items_anywhere=False):
    """{chest hash: item}, and with shop slots given (shops.survey()) also {slot id: item}: the shops' items join the
    pool (shops.assign). Without slots the result is the same as before shop shuffle existed (same seeds).
    dungeon_items_anywhere: small keys, maps, compasses and boss keys may also go in field-area chests (never another
    dungeon: there the pickup would count for that dungeon) and, with shops, on shop shelves; each then counts for
    its own dungeon (chests: keycredit.py, dungeoncredit.py; shelves: shopdungeon.py).
    Returns (assignment, shop, origins: {chest hash or shop slot id: dungeon} for the dungeon items placed)."""
    assignment, origins, shop_pre = {}, {}, {}
    slot_ids = [s["id"] for s in slots or []]
    dungeon_items = collections.defaultdict(list)
    for loc in locs:
        if loc["item"].startswith("Item_Dungeon_") and loc["area"] in DUNGEONS:
            dungeon_items[loc["area"]].append(loc)
    field = [l for l in locs if l["area"] not in DUNGEONS and l["area"] not in NO_CREDIT_AREAS]
    if slots:   # (the shelves the items that can't go in a chest need, kept for them: shops.assign)
        import shops
        shop_only = sum(1 for i in [l["item"] for l in locs] + [s["item"] for s in slots] if shops.chest_form(i) is None)
    for area, group in sorted(dungeon_items.items()):
        if dungeon_items_anywhere:   # keys first, into their own dungeon; then the others, own dungeon or field
            keys = [l for l in group if l["item"] not in DUNGEON_CREDITED]
            others = [l for l in group if l["item"] in DUNGEON_CREDITED]
            own = [l for l in locs if l["area"] == area and l["hash"] not in assignment]
            rng.shuffle(own)
            for loc, chest in zip(keys, own):
                assignment[chest["hash"]] = loc["item"]
                origins[chest["hash"]] = area
            for loc in others:   # (the credit is the chest's event: not in chests with a story event of their own)
                room = [l for l in locs if l["area"] == area or l in field and not has_story_event(l)]
                room = [l["hash"] for l in room if l["hash"] not in assignment]
                if len(slot_ids) - len(shop_pre) > shop_only if slots else False:
                    room += [s for s in slot_ids if s not in shop_pre]
                place = rng.choice(room)
                if place in slot_ids:
                    shop_pre[place] = loc["item"]
                else:
                    assignment[place] = loc["item"]
                origins[place] = area
            continue
        dungeon_chests = [l for l in locs if l["area"] == area]
        rng.shuffle(dungeon_chests)
        for loc, chest in zip(group, dungeon_chests):
            assignment[chest["hash"]] = loc["item"]
            origins[chest["hash"]] = area
    placed = {id(l) for g in dungeon_items.values() for l in g}
    free = [l for l in locs if l["hash"] not in assignment]
    pool = [l["item"] for l in locs if id(l) not in placed]
    if slots:
        import shops
        shop, chest_items = shops.assign([s for s in slot_ids if s not in shop_pre], free,
                                         pool + [s["item"] for s in slots], rng)
        for chest, item in zip(free, chest_items):
            assignment[chest["hash"]] = item
        shop.update(shop_pre)
        return assignment, shop, origins
    rng.shuffle(pool)
    assert len(pool) == len(free)
    for chest, item in zip(free, pool):
        assignment[chest["hash"]] = item
    return assignment, {}, origins


def build(seed, out, flags, intro_skip=True, cutscene_skip=True, bombs_need_bracelet=False, free_magic=False,
          shuffle_chests=True, place=None, start_deku_nut=True, keep_items=(), shuffle_shops=False,
          bombchus_without_bag=False, bombs_give_bag=False, dungeon_items_anywhere=False):
    """Write the overlay for seed into out (replacing it); return a one-line summary.

    place: {chest hash or shop slot id: item} for test seeds. Each item is swapped into its place from wherever the
    shuffle put it (that place gets the one it held), so the items as a whole stay the same.
    keep_items: items (e.g. Item_Sword_Kokiri) whose chests are left out of the shuffle and keep them (and their
    item event, so they play as in the game).
    shuffle_shops: the shops' items join the pool (shops.py); needs shuffle_chests.
    """
    import cutscenes
    import shops
    if cutscene_skip:
        flags = {**flags, **cutscenes.FLAGS}
    if bombchus_without_bag:
        flags = {**flags, **BOMBCHUS_WITHOUT_BAG}
    rng = random.Random(seed)
    locs = load_chests()
    slots = shops.survey() if shuffle_shops and shuffle_chests else []
    keep_items = set(keep_items)
    kept = [l for l in locs if l["item"] in keep_items]
    missing = keep_items - {l["item"] for l in kept}
    assert not missing, f"keep: no chest holds {missing}"
    shuffled = [l for l in locs if l["item"] not in keep_items]
    if shuffle_chests:
        assignment, shop, origins = shuffle(shuffled, rng, slots, dungeon_items_anywhere)
    else:
        assignment, shop = {l["hash"]: l["item"] for l in shuffled}, {}
        origins = {l["hash"]: l["area"] for l in shuffled if l["item"].startswith("Item_Dungeon_")}
    assignment.update({l["hash"]: l["item"] for l in kept})
    for l in kept:
        print(f"  kept: {l['item']} stays in chest {l['hash']} ({l['area']})")
    kept_hashes = {l["hash"] for l in kept}
    place = {(int(h) if str(h).isdigit() else h): item for h, item in (place or {}).items()}
    where = {**assignment, **shop}   # every place -> item, chests and shop slots

    area_of = {l["hash"]: l["area"] for l in locs}
    loc_of = {l["hash"]: l for l in locs}

    def fits(p, item, origin=None):
        """Whether p can hold item (a dungeon item of dungeon origin: only there, or in a field area if credited, or
        on a shelf with dungeon items anywhere)."""
        if p in shop and item.startswith("Item_Dungeon_"):
            return dungeon_items_anywhere and item in DUNGEON_CREDITED
        if (shops.shop_form(item) if p in shop else shops.chest_form(item)) is None:
            return False
        if item.startswith("Item_Dungeon_") and origin:
            a = area_of.get(p)
            if a == origin:
                return True
            if p in loc_of and has_story_event(loc_of[p]):
                return False
            return item in DUNGEON_CREDITED and a not in DUNGEONS and a not in NO_CREDIT_AREAS
        return True

    for h, item in place.items():
        home = None
        if "@" in item:   # ITEM@DUNGEON: that dungeon's copy (maps, compasses, keys)
            item, home = item.split("@", 1)
        assert h in where and h not in kept_hashes, f"place: no shuffled chest or shop slot {h}"
        assert fits(h, item, home), f"place: {item} can't go in {h}"
        if where[h] != item or (home and origins.get(h) != home):   # swap with a copy of item that fits
            free = lambda c: c not in place and c not in kept_hashes and c != h
            holders = [c for c, i in where.items() if i == item and free(c) and (home is None or origins.get(c) == home)]
            assert holders, f"place: no {item} left to move"
            src = next((c for c in holders if fits(c, where[h], origins.get(h))), None)
            org = lambda c: origins.pop(c, None)   # (a dungeon item's own dungeon moves with it)
            if src is not None:
                where[src], where[h] = where[h], item
                o_src, o_h = org(src), org(h)
                moved_origins = {h: o_src, src: o_h}
            else:   # (e.g. a rupee can't go on a shelf): a third place t takes h's item, src takes t's
                src = holders[0]
                t = next((c for c, i in where.items() if free(c) and c != src and fits(c, where[h], origins.get(h))
                          and fits(src, i, origins.get(c))), None)
                assert t is not None, f"place: can't make room for {item} in {h}"
                where[t], where[src], where[h] = where[h], where[t], item
                o_t, o_src, o_h = org(t), org(src), org(h)
                moved_origins = {t: o_h, src: o_t, h: o_src}
            origins.update({c: o for c, o in moved_origins.items() if o})
        print(f"  placed: {item} in {'shop slot' if h in shop else 'chest'} {h}")
    assignment = {h: where[h] for h in assignment}
    shop = {s: where[s] for s in shop}
    if shop:
        n = shops.no_repeats(shop, set(place), random.Random(f"{seed}/shops"))
        if n:
            print(f"  shops: {n} swaps so that no shop sells the same item twice")
    assert sorted(list(assignment.values()) + list(shop.values())) == \
        sorted([l["item"] for l in locs] + [s["item"] for s in slots]), "items lost or duplicated"

    out = Path(out)
    staging = out.with_name(out.name + ".new")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    per_file = collections.defaultdict(dict)
    drops = collections.defaultdict(dict)
    dropped_events = {}
    bag_checks = collections.defaultdict(dict)   # map file -> {chest Hash: BOMB_CHECK}
    chest_kind = collections.defaultdict(dict)   # map file -> {chest Hash: its new actor kind (CHECK_CHEST)}
    import dungeoncredit
    import keycredit
    credits = {}   # chest hash -> (dungeon, flag): a dungeon item outside its dungeon, credited by map logic
    credit_groups = collections.defaultdict(dict)   # map file -> {chest Hash: logic module name}
    for loc in locs:
        item, home = assignment[loc["hash"]], origins.get(loc["hash"])
        if item in DUNGEON_CREDITED and home and home != loc["area"]:
            assert loc["area"] not in DUNGEONS and loc["area"] not in NO_CREDIT_AREAS, \
                f"{item} of {home} placed in {loc['area']}: its pickup would count for that area"
            credits[loc["hash"]] = (home, dungeoncredit.credit_flag(item, home))
            assert not has_story_event(loc), f"{item} in story chest {loc['hash']}"
            for f in loc["files"]:   # (its event has Navi name the dungeon and adds a key: keycredit.py;
                bag_checks[f].setdefault(loc["hash"], {})["GetEventName"] = \
                    keycredit.event_name(keycredit.KINDS[item], home)
                if item != "Item_Dungeon_SmallKey":   # a map, compass or boss key's flag: map logic, dungeoncredit.py)
                    credit_groups[f][loc["hash"]] = dungeoncredit.module_name(loc["hash"])
        elif item.startswith("Item_Dungeon_") and home and home != loc["area"]:
            raise AssertionError(f"{item} of {home} placed in {loc['area']}: not credited")
    for loc in locs:
        actor = chest_actor(assignment[loc["hash"]], bombs_give_bag)
        if needs_bag_check(assignment[loc["hash"]], bombs_give_bag, loc["actor"]):
            for f in loc["files"]:
                bag_checks[f][loc["hash"]] = dict(BOMB_CHECK)
                chest_kind[f][loc["hash"]] = CHECK_CHEST[loc["actor"]]
        if actor != loc["item"]:
            for f in loc["files"]:
                per_file[f][loc["hash"]] = actor
                if is_item_event(loc["event"]):
                    drops[f][loc["hash"]] = ["GetEventName"]
                    dropped_events[loc["hash"]] = loc["event"]
    import shopdungeon
    dungeon_slots = sorted(s for s, v in shop.items() if v.startswith("Item_Dungeon_"))
    instances = {sid: shopdungeon.Instance(shop[sid], origins[sid], n + 1) for n, sid in enumerate(dungeon_slots)}
    shelf = collections.defaultdict(dict)   # map file -> {placed shop item Hash: its new Shop_ actor}
    for s in slots:
        if shop[s["id"]] != s["item"]:
            for rel, h in s["copies"]:
                shelf[rel][h] = instances[s["id"]].shelf if s["id"] in instances else shops.shop_form(shop[s["id"]])

    sold_out = collections.defaultdict(dict)   # map file -> {placed shop item Hash: (item, its own sold flag or None)}
    for s in slots:
        if s["id"] in instances or shops.sold_out_flag(shop[s["id"]], gd_hashes("Bool")):
            own = instances[s["id"]].sold_flag if s["id"] in instances else None
            for rel, h in s["copies"]:
                sold_out[rel][h] = (shop[s["id"]], own)
    sold_bools = gd_hashes("Bool") | {murmur3(i.sold_flag.encode()) for i in instances.values()}
    exps = {}
    growth = {}
    for rel in sorted(set(per_file) | set(shelf) | set(sold_out) | set(bag_checks) | set(credit_groups)):
        data, zdict = read_original(rel)
        new = data
        if rel in per_file:
            new = apply_items(new, per_file[rel], drops[rel])
        if rel in bag_checks:
            new = set_chest_checks(new, bag_checks[rel])
            new = set_gyaml(new, chest_kind[rel])
        if rel in credit_groups:
            new = dungeoncredit.add_groups(new, credit_groups[rel])
        if rel in shelf:
            new = set_gyaml(new, shelf[rel])
        if rel in sold_out:
            from nxfmt import Byml
            old = {a["Hash"]: a.get("Presence") for a in Byml(data).root["Actors"] if a.get("Hash") in sold_out[rel]}
            pres = {}
            for h, (item, own) in sold_out[rel].items():
                p, need = shops.presence_for(old[h], item, sold_bools, gd_hashes("BoolExp"), own)
                pres[h] = p
                exps.update(need)
            new = shops.set_presences(new, pres)
        write_zs(rel, staging, new, zdict)
        growth[rel[:-3]] = len(new) - len(data)

    if slots:
        ddata, ddict = read_original(shops.DISPLAY_SHOP)
        new = shops.unlock_bombs(ddata)
        if instances:   # (buying a dungeon item credits its dungeon and sets its sold flag: shopdungeon.py)
            new = shopdungeon.add_purchase_chain(new, [instances[s] for s in dungeon_slots])
            growth[shops.DISPLAY_SHOP[:-3]] = len(new) - len(ddata)
            print(f"  shops: {len(instances)} dungeon items on shelves, credited to their dungeon when bought")
        write_zs(shops.DISPLAY_SHOP, staging, new, ddict)
        print("  shops: bombs for sale before the Goron's Ruby (DisplayShop checks)")

    new_sizes = {}
    for h, (home, flag) in sorted(credits.items()):   # map logic for the Bool ones (maps, compasses, boss keys)
        if assignment[h] == "Item_Dungeon_SmallKey":
            continue
        name = dungeoncredit.module_name(h)
        rel = dungeoncredit.module_path(name)[0]
        dst = staging / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(dungeoncredit.credit_module(name, assignment[h], home))
        new_sizes[rel] = dst.stat().st_size + dungeoncredit.SIZE_MARGIN
    kinds = sorted({(keycredit.KINDS[assignment[h]], home) for h, (home, _) in credits.items()})
    if kinds or instances:   # messages: Navi's (chest events) and the shelf items' names, in every language
        for rel in keycredit.mals_files():
            data, zdict = read_original(rel)
            new = keycredit.add_messages(data, kinds) if kinds else data
            if instances:
                new = shopdungeon.add_messages(new, [instances[s] for s in dungeon_slots])
            write_zs(rel, staging, new, zdict)
            growth[rel[:-3]] = len(new) - len(data)
    if kinds:   # one event (flow + rows + Navi's message) per item kind and dungeon with such items outside it
        _, fdict = read_original(keycredit.SRC_FLOW)
        for kind, dn in kinds:
            rel = keycredit.flow_path(kind, dn)
            flow = keycredit.make_flow(kind, dn)
            write_zs(rel, staging, flow, fdict)
            new_sizes[rel[:-3]] = len(flow) + keycredit.SIZE_MARGIN
        for rel in (keycredit.EVENT_SETTING, keycredit.EVENT_INFO):
            data, zdict = read_original(rel)
            new = keycredit.add_rows(rel, data, kinds)
            write_zs(rel, staging, new, zdict)
            growth[rel[:-3]] = len(new) - len(data)
    if credits:
        print(f"  dungeon items: {len(credits)} outside their dungeon, credited to it by chest events "
              f"({len(kinds)} events, Navi names the dungeon)")
    sold = sorted({i for s, i in shop.items() if s not in instances and
                   (shops.shop_form(i) or "").startswith(shops.RANDO_PREFIX)})   # new shelf actors
    prices = {}
    price_rng = random.Random(f"{seed}/prices")
    shelf_rows = {}   # dungeon items on shelves: {shelf actor: (item, price, its name)}
    for sid in dungeon_slots:
        inst = instances[sid]
        shelf_rows[inst.shelf] = (inst.item, shops.price_for(inst.item, price_rng), inst.name)
    if sold or shelf_rows:
        prices = {i: shops.price_for(i, price_rng) for i in sold}
        _, pack_dict = read_original(f"Pack/Actor/{shops.TEMPLATE}.pack.zs")
        packs = [(f"{shops.RANDO_PREFIX}{i}", i, prices[i]) for i in sold] + \
                [(name, i, p) for name, (i, p, _) in sorted(shelf_rows.items())]
        for name, item, price in packs:
            rel = f"Pack/Actor/{name}.pack.zs"
            pack = shops.make_shop_actor(item, price, name)
            write_zs(rel, staging, pack, pack_dict)
            new_sizes[rel[:-3]] = len(pack) + shops.PACK_SIZE_MARGIN
        for rel in (shops.ACTOR_INFO, shops.GAME_ACTOR_INFO):
            data, zdict = read_original(rel)
            new = shops.add_rows(rel, data, prices, shelf_rows)
            write_zs(rel, staging, new, zdict)
            growth[rel[:-3]] = len(new) - len(data)
        print(f"  shops: {sum(1 for s in slots if shop[s['id']] != s['item'])} of {len(slots)} shop slots sell "
              f"something new, {len(sold)} chest items on shelves (new shop actors)")

    if flags or exps or instances:
        gdl_rel = GDL.relative_to(ROMFS).as_posix()
        orig, gdict = read_original(gdl_rel)
        gdata = orig
        bools = {murmur3(n.encode()): (n, v) for n, v in flags.items() if isinstance(v, bool)}
        ints = {murmur3(n.encode()): (n, v) for n, v in flags.items() if not isinstance(v, bool)}
        if bools:
            gdata = set_bool_defaults(gdata, bools)
        if ints:
            gdata = set_int_defaults(gdata, ints)
        if instances:   # (rewrites: after the in-place edits) a saved sold flag per dungeon item on a shelf
            gdata = shopdungeon.add_sold_flags(gdata, [instances[s] for s in dungeon_slots])
        if exps:   # (after the in-place edits: this one rewrites the file)
            gdata = shops.add_bool_exps(gdata, exps)
        if len(gdata) != len(orig):
            growth[gdl_rel[:-3]] = len(gdata) - len(orig)
        write_zs(gdl_rel, staging, gdata, gdict)
    if sold_out:
        print(f"  shops: {sum(len(v) for v in sold_out.values())} shelf copies leave once their item is owned "
              f"({len(exps)} new BoolExp conditions)")

    if all(flags.get(k) == v for k, v in OPEN_DOOR.items()):
        for rel, patch in ((DOOR_LOGIC, wire_door_to_flag), (SWORD_LOGIC, first_draw_before_allsage)):
            dst = staging / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(patch((ROMFS / rel).read_bytes()))

    if intro_skip:
        odata, odict = read_original(OPENING)
        write_zs(OPENING, staging, skip_intro(odata), odict)

    if cutscene_skip:
        for rel, patch in cutscenes.PATCHES:
            cdata, cdict = read_original(rel)
            write_zs(rel, staging, patch(cdata), cdict)

    if not bombs_need_bracelet:
        bdata, bdict = read_original(BOMB_PACK)
        write_zs(BOMB_PACK, staging, bombs_without_bracelet(bdata), bdict)
    swapped = sum(1 for l in locs if chest_actor(assignment[l["hash"]], bombs_give_bag) !=
                  shops.chest_form(assignment[l["hash"]]))
    if swapped:
        print(f"  bombs: {swapped} chest bomb packs hold Bombchus until the Bomb Bag is owned (bombs don't bring it)")

    magic_packs = []
    if not free_magic:
        import itempatches
        for rel in itempatches.magic_packs():
            mdata, mdict = read_original(rel)
            new, changed = itempatches.no_free_magic(mdata)
            assert changed, f"{rel}: no magic entry found"
            write_zs(rel, staging, new, mdict)
            magic_packs.append(rel)
        idata, idict = read_original(itempatches.POUCH_INFO)
        new, rows = itempatches.rsdb_no_free_magic(idata)
        assert rows, "PouchActorInfo: no magic rows found"
        pouch_info = (idata, new, idict)
        print(f"  magic: {len(rows)} items no longer give the magic meter (only Great Fairies do): "
              f"item table rows and {len(magic_packs)} item packs")
    else:
        idata, idict = read_original(shops.POUCH_INFO)
        pouch_info = (idata, idata, idict)
    if pouch_info[1] != pouch_info[0] or prices or shelf_rows:   # (the magic rows, then the shop rows)
        idata, new, idict = pouch_info
        if prices or shelf_rows:
            new = shops.add_rows(shops.POUCH_INFO, new, prices, shelf_rows)
        write_zs(shops.POUCH_INFO, staging, new, idict)
        growth[shops.POUCH_INFO[:-3]] = len(new) - len(idata)

    if start_deku_nut:
        import ainbedit
        orig = (ROMFS / GAME_START).read_bytes()
        new = ainbedit.add_pushes(orig, START_PUSHES)
        bad = ainbedit.check_pushes(orig, new, START_PUSHES)
        assert not bad, f"new-game setup patch: {bad}"
        dst = staging / GAME_START
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(new)
        growth[GAME_START] = len(new) - len(orig)
        print("  start: the new-game setup also gives " +
              ", ".join(f"{p.split('/')[-1].split('.')[0]} x{n}" for p, n in START_PUSHES))

    from nxfmt import rstb_path, unzs
    table_rel, table_src = rstb_path()   # (Hookshot's own copy when it has one: nxfmt.HOOKSHOT_RSTB)
    _, tdict = read_original(table_rel)
    tdata = unzs(table_src.read_bytes())
    write_zs(table_rel, staging, add_sizes(raise_sizes(tdata, growth), new_sizes), tdict)

    moved = sum(1 for l in locs if chest_actor(assignment[l["hash"]], bombs_give_bag) != l["item"])
    lines = [f"Chest randomizer, no logic. Seed {seed}. Chests shuffled: {shuffle_chests}. Flags: {flags or 'none changed'}. Intro skip: {intro_skip}. "
             f"Cutscene skips: {cutscene_skip}. Bombs need the Goron's Bracelet: {bombs_need_bracelet}. "
             f"Magic items give magic: {free_magic}.",
             f"{len(locs)} chests, {moved} hold something new.", ""]
    for loc in sorted(locs, key=lambda l: (l["area"], l["files"][0], l["hash"])):
        item = assignment[loc["hash"]]
        actor = chest_actor(item, bombs_give_bag)
        mark = "" if actor == loc["item"] else "   (was " + loc["item"] + ")"
        if loc["hash"] in dropped_events:
            mark += f" [event {dropped_events[loc['hash']]} removed]"
        if loc["hash"] in kept_hashes:
            mark += "   [kept]"
        if loc["hash"] in credits:
            mark += f"   [counts for {credits[loc['hash']][0]}]"
        shown = item if actor == item else f"{actor} (for {item})"
        lines.append(f"{loc['area']:<22} {loc['actor']:<24} {loc['hash']:>20}  {shown}{mark}")
    if slots:
        lines += ["", f"Shops: {len(slots)} slots (shop slot id = area/shopkeeper/shelf place).", ""]
        for s in slots:
            item = shop[s["id"]]
            price = ""   # (the price set for a new shop actor isn't what the shop asks yet: not shown)
            mark = "" if item == s["item"] else "   (was " + s["item"] + ")"
            if s["id"] in instances:
                mark += f"   [counts for {instances[s['id']].dungeon}]"
            lines.append(f"{s['id']:<46} {item}{price}{mark}")
    (staging / "spoiler.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (staging / "seed.json").write_text(json.dumps({"seed": seed, "flags": flags, "intro_skip": intro_skip,
                                                   "cutscene_skip": cutscene_skip,
                                                   "bombs_need_bracelet": bombs_need_bracelet,
                                                   "free_magic": free_magic, "magic_packs": magic_packs,
                                                   "shuffle_chests": shuffle_chests, "start_deku_nut": start_deku_nut,
                                                   "keep_items": sorted(keep_items),
                                                   "shuffle_shops": bool(slots), "shop": shop,
                                                   "bombchus_without_bag": bombchus_without_bag,
                                                   "bombs_give_bag": bombs_give_bag,
                                                   "dungeon_items_anywhere": dungeon_items_anywhere,
                                                   "size_table_base": size_table_base(),
                                                   "dungeon_credits": {str(h): list(v) for h, v in credits.items()},
                                                   "shop_dungeon": {s: [i.item, i.dungeon, i.n, shelf_rows[i.shelf][1]]
                                                                    for s, i in instances.items()},
                                                   "shop_prices": prices,
                                                   "place": {str(h): i for h, i in place.items()},
                                                   "dropped_events": {str(h): e for h, e in dropped_events.items()}}),
                                       encoding="utf-8")
    shutil.rmtree(out, ignore_errors=True)
    staging.rename(out)
    return f"seed {seed}: {len(per_file)} map files changed, {moved} of {len(locs)} chests hold something new"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--out", default=str(HERE.parent / "overlay"))
    ap.add_argument("--closed-forest", action="store_true", help="keep the Kokiri guarding the forest exit")
    ap.add_argument("--closed-door", action="store_true", help="keep the Door of Time shut")
    ap.add_argument("--keep-intro", action="store_true", help="play the opening movie and Navi waking Link")
    ap.add_argument("--keep-cutscenes", action="store_true", help="play Saria's scenes in full")
    ap.add_argument("--bombs-need-bracelet", action="store_true",
                    help="keep the game's rule: bombs can't be held without the Goron's Bracelet")
    ap.add_argument("--free-magic", action="store_true",
                    help="keep the game's failsafe: the Lens, spells and magic arrows also give the magic meter")
    ap.add_argument("--no-deku-nut", action="store_true",
                    help="don't start with Deku Nuts (0 nuts); then an adult item found first can block all items")
    ap.add_argument("--place", action="append", default=[], metavar="HASH=ITEM",
                    help="test seeds: put ITEM in chest HASH (repeatable), e.g. 10831981390482426294=Item_BombBag_Small")
    ap.add_argument("--keep", action="append", default=[], metavar="ITEM",
                    help="leave the chest holding ITEM alone (repeatable), e.g. Item_Sword_Kokiri")
    ap.add_argument("--dungeon-items-anywhere", action="store_true",
                    help="maps, compasses and boss keys may be outside dungeons too, credited to their own dungeon")
    ap.add_argument("--bombs-give-bag", action="store_true",
                    help="the first bombs found give the Bomb Bag (as Deku Nuts and Sticks give theirs)")
    ap.add_argument("--bombchus-without-bag", action="store_true",
                    help="Bombchus can be held without the Bomb Bag (a save starts with bomb capacity 10)")
    ap.add_argument("--shuffle-shops", action="store_true",
                    help="the shops' items join the pool: shop items in chests and other shops, chest items in shops")
    args = ap.parse_args()
    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(10**9)
    flags = {**({} if args.closed_forest else OPEN_FOREST), **({} if args.closed_door else OPEN_DOOR)}
    place = dict(p.split("=", 1) for p in args.place)
    t0 = time.time()
    print(build(seed, args.out, flags, not args.keep_intro, not args.keep_cutscenes, args.bombs_need_bracelet,
                args.free_magic, place=place, start_deku_nut=not args.no_deku_nut, keep_items=args.keep,
                shuffle_shops=args.shuffle_shops, bombchus_without_bag=args.bombchus_without_bag,
                bombs_give_bag=args.bombs_give_bag, dungeon_items_anywhere=args.dungeon_items_anywhere),
          f"({time.time() - t0:.0f} s)")


if __name__ == "__main__":
    main()
