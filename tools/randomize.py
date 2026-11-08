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
from bymledit import read_original, write_zs, apply_items, set_bool_defaults, set_int_defaults, raise_sizes
from gamedata import GDL, murmur3

HERE = Path(__file__).parent
DUNGEONS = {"InsideTheDekuTree", "DodongoCavern", "InsideJabuJabuBelly", "ForestTemple", "FireTemple", "WaterTemple",
            "ShadowTemple", "SpiritTemple", "BottomOfTheWell", "IceCavern", "GerudoTrainingGround",
            "InsideGanonCastle", "GanonCastle"}
EXCLUDED_AREAS = {"DreamWorld"}
STORY_EVENTS = {"SageOfWater_MelodyGet_Water"}   # "Get" in the name, but it teaches a song: keep it


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


def shuffle(locs, rng):
    assignment = {}
    dungeon_items = collections.defaultdict(list)
    for loc in locs:
        if loc["item"].startswith("Item_Dungeon_") and loc["area"] in DUNGEONS:
            dungeon_items[loc["area"]].append(loc)
    for area, group in sorted(dungeon_items.items()):
        dungeon_chests = [l for l in locs if l["area"] == area]
        rng.shuffle(dungeon_chests)
        for loc, chest in zip(group, dungeon_chests):
            assignment[chest["hash"]] = loc["item"]
    placed = {id(l) for g in dungeon_items.values() for l in g}
    free = [l for l in locs if l["hash"] not in assignment]
    pool = [l["item"] for l in locs if id(l) not in placed]
    rng.shuffle(pool)
    assert len(pool) == len(free)
    for chest, item in zip(free, pool):
        assignment[chest["hash"]] = item
    return assignment


def build(seed, out, flags, intro_skip=True, cutscene_skip=True, bombs_need_bracelet=False, free_magic=False,
          shuffle_chests=True, place=None, start_deku_nut=True, keep_items=()):
    """Write the overlay for seed into out (replacing it); return a one-line summary.

    place: {chest hash: item} for test seeds. Each item is swapped into its chest from wherever the shuffle put it
    (that chest gets the one it held), so the items as a whole stay the same.
    keep_items: items (e.g. Item_Sword_Kokiri) whose chests are left out of the shuffle and keep them (and their
    item event, so they play as in the game).
    """
    import cutscenes
    if cutscene_skip:
        flags = {**flags, **cutscenes.FLAGS}
    rng = random.Random(seed)
    locs = load_chests()
    keep_items = set(keep_items)
    kept = [l for l in locs if l["item"] in keep_items]
    missing = keep_items - {l["item"] for l in kept}
    assert not missing, f"keep: no chest holds {missing}"
    shuffled = [l for l in locs if l["item"] not in keep_items]
    assignment = shuffle(shuffled, rng) if shuffle_chests else {l["hash"]: l["item"] for l in shuffled}
    assignment.update({l["hash"]: l["item"] for l in kept})
    for l in kept:
        print(f"  kept: {l['item']} stays in chest {l['hash']} ({l['area']})")
    kept_hashes = {l["hash"] for l in kept}
    place = {int(h): item for h, item in (place or {}).items()}
    for h, item in place.items():
        assert h in assignment and h not in kept_hashes, f"place: no shuffled chest {h}"
        src = next((c for c, i in assignment.items() if i == item and c not in place and c not in kept_hashes), None)
        assert src is not None or assignment[h] == item, f"place: no {item} left in the chests"
        if src is not None:
            assignment[src], assignment[h] = assignment[h], item
        print(f"  placed: {item} in chest {h}")
    assert sorted(assignment.values()) == sorted(l["item"] for l in locs), "items lost or duplicated"

    out = Path(out)
    staging = out.with_name(out.name + ".new")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    per_file = collections.defaultdict(dict)
    drops = collections.defaultdict(dict)
    dropped_events = {}
    for loc in locs:
        if assignment[loc["hash"]] != loc["item"]:
            for f in loc["files"]:
                per_file[f][loc["hash"]] = assignment[loc["hash"]]
                if is_item_event(loc["event"]):
                    drops[f][loc["hash"]] = ["GetEventName"]
                    dropped_events[loc["hash"]] = loc["event"]

    growth = {}
    for rel, items in sorted(per_file.items()):
        data, zdict = read_original(rel)
        new = apply_items(data, items, drops[rel])
        write_zs(rel, staging, new, zdict)
        growth[rel[:-3]] = len(new) - len(data)

    if flags:
        gdl_rel = GDL.relative_to(ROMFS).as_posix()
        gdata, gdict = read_original(gdl_rel)
        bools = {murmur3(n.encode()): (n, v) for n, v in flags.items() if isinstance(v, bool)}
        ints = {murmur3(n.encode()): (n, v) for n, v in flags.items() if not isinstance(v, bool)}
        gdata = set_bool_defaults(gdata, bools)
        if ints:
            gdata = set_int_defaults(gdata, ints)
        write_zs(gdl_rel, staging, gdata, gdict)

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
        write_zs(itempatches.POUCH_INFO, staging, new, idict)
        print(f"  magic: {len(rows)} items no longer give the magic meter (only Great Fairies do): "
              f"item table rows and {len(magic_packs)} item packs")

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

    table_rel = RSTB.relative_to(ROMFS).as_posix()
    tdata, tdict = read_original(table_rel)
    write_zs(table_rel, staging, raise_sizes(tdata, growth), tdict)

    moved = sum(1 for l in locs if assignment[l["hash"]] != l["item"])
    lines = [f"Chest randomizer, no logic. Seed {seed}. Chests shuffled: {shuffle_chests}. Flags: {flags or 'none changed'}. Intro skip: {intro_skip}. "
             f"Cutscene skips: {cutscene_skip}. Bombs need the Goron's Bracelet: {bombs_need_bracelet}. "
             f"Magic items give magic: {free_magic}.",
             f"{len(locs)} chests, {moved} hold something new.", ""]
    for loc in sorted(locs, key=lambda l: (l["area"], l["files"][0], l["hash"])):
        mark = "" if assignment[loc["hash"]] == loc["item"] else "   (was " + loc["item"] + ")"
        if loc["hash"] in dropped_events:
            mark += f" [event {dropped_events[loc['hash']]} removed]"
        if loc["hash"] in kept_hashes:
            mark += "   [kept]"
        lines.append(f"{loc['area']:<22} {loc['actor']:<24} {loc['hash']:>20}  {assignment[loc['hash']]}{mark}")
    (staging / "spoiler.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (staging / "seed.json").write_text(json.dumps({"seed": seed, "flags": flags, "intro_skip": intro_skip,
                                                   "cutscene_skip": cutscene_skip,
                                                   "bombs_need_bracelet": bombs_need_bracelet,
                                                   "free_magic": free_magic, "magic_packs": magic_packs,
                                                   "shuffle_chests": shuffle_chests, "start_deku_nut": start_deku_nut,
                                                   "keep_items": sorted(keep_items),
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
    args = ap.parse_args()
    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(10**9)
    flags = {**({} if args.closed_forest else OPEN_FOREST), **({} if args.closed_door else OPEN_DOOR)}
    place = dict(p.split("=", 1) for p in args.place)
    t0 = time.time()
    print(build(seed, args.out, flags, not args.keep_intro, not args.keep_cutscenes, args.bombs_need_bracelet,
                args.free_magic, place=place, start_deku_nut=not args.no_deku_nut, keep_items=args.keep),
          f"({time.time() - t0:.0f} s)")


if __name__ == "__main__":
    main()
