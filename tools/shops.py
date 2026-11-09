"""Shop shuffle: the shops' items join the item pool, so shop items can turn up in chests and other shops, and chest
items in shops.

How the game's shops work (found 2026-11-09):
- A shop is a shopkeeper actor in a map file (Npc_Kokiri_001_Shop_Child, ...) with ForSale links to the items on its
  shelves. Each item is an actor of its own kind, Shop_<something> (Shop_DekuNutBundle_01, Shop_Weapon_Shield_Deku),
  with a ShopItemIndex (its place on the shelf, 1 to 7; the eighth has none).
- A Shop_ actor's pack holds what it gives (PouchContentParam: PouchActor, or BundleActor x BundleActorNum), its
  model and its price (PriceParam). The game's compiled tables have a row for it too: RSDB ActorInfo (model, heap
  size), GameActorInfo and PouchActorInfo (pouch behaviour, BuyingPrice). Rows are sorted by __RowId.
- The shop's own copies: a shop has a child and an adult copy (KokiriForest_Child_Static / _Adult_Static, and
  _Alternative_ variants), and Kokiri Forest a second child copy before the Deku Tree (..._ShortStock). One shop slot
  here is (area, shopkeeper without _Child/_Adult/_ShortStock, ShopItemIndex), and all its copies get the same item.
  Before the Deku Tree the Kokiri shop (its ShortStock copy) sells only shelf places 2, 6 and 7; the rest show
  sold-out boards (seen in game 2026-11-09). A future logic must count the others as reachable only after Gohma.

The shuffle:
- A shop item moved to another shop keeps its own Shop_ actor (only the placed actor's Gyaml changes), so its price
  and model come along.
- A chest item moved to a shop gets a new actor, Shop_Rando_<item>: a Shop_ pack built from Shop_Armor_Goron's
  shelf parts and the item's own model, a PouchContentParam whose PouchActor is the item, and rows added to the three
  RSDB tables (copies of the item's own rows, as the game's Shop_Mask_ rows copy Item_Mask_ ones).
- A shop item moved to a chest is given in the form a chest or the field already uses (CHEST_FORM): TBox_Shield_Deku,
  Item_BombBundle_Small, Obj_Armor_Goron, ...
- Kept in shops (shuffled between shops only): bottle contents (they need an empty bottle), Recovery Hearts and the
  small magic jar (no chest form). Left alone: the Happy Mask Shop (a borrowing quest), dungeon items, and chest
  items that aren't pouch items (rupees, the chests' own bundle actors, minigame keys).
- Untested parts are marked as such in the notes below; the first test seed checks them.
"""
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from nxfmt import ROMFS, load_byml, parse_sarc, unzs
import bymlw

HERE = Path(__file__).parent
SHOPS_JSON = HERE / "shops.json"
EXCLUDED_KEEPERS = {"Npc_Hylia_023_Shop"}   # the Happy Mask Shop
TEMPLATE = "Shop_Armor_Goron"   # a one-item shop actor whose shelf parts are the plain Shop_Item_Base ones
RANDO_PREFIX = "Shop_Rando_"
POUCH_INFO = "RSDB/PouchActorInfo.Product.111.rstbl.byml.zs"
ACTOR_INFO = "RSDB/ActorInfo.Product.111.rstbl.byml.zs"
GAME_ACTOR_INFO = "RSDB/GameActorInfo.Product.111.rstbl.byml.zs"
PACK_SIZE_MARGIN = 1024   # (the game's pack entries are their size + ~340)

# A shop item's form in a chest: what a chest (or the field) already gives for it. None: shops only.
CHEST_FORM = {
    "Shop_Weapon_Shield_Deku": "TBox_Shield_Deku",
    "Shop_Weapon_Shield_Hylian": "TBox_Shield_Hylian",
    "Shop_Armor_Goron": "Obj_Armor_Goron",
    "Shop_Armor_Zora": "Obj_Armor_Zora",
    "Shop_ArrowBundle_01": "TBox_ArrowBundle_Small",
    "Shop_ArrowBundle_02": "TBox_ArrowBundle_Medium",
    "Shop_ArrowBundle_03": "TBox_ArrowBundle_Large",
    "Shop_BombBundle_01": "Item_BombBundle_Small",
    "Shop_BombBundle_02": "Item_BombBundle_Small",
    "Shop_BombBundle_03": "Item_BombBundle_Small",
    "Shop_BombBundle_04": "Item_BombBundle_Small",
    "Shop_BombchuBundle_01": "Item_BombchuBundle_Medium",
    "Shop_BombchuBundle_02": "Item_BombchuBundle_Large",
    "Shop_BombchuBundle_03": "Item_BombchuBundle_Small",
    "Shop_BombchuBundle_04": "Item_BombchuBundle_Small",
    "Shop_BombchuBundle_05": "Item_BombchuBundle_Small",
    "Shop_BombchuBundle_06": "Item_BombchuBundle_Medium",
    "Shop_BombchuBundle_07": "Item_BombchuBundle_Medium",
    "Shop_BombchuBundle_08": "Item_BombchuBundle_Large",
    "Shop_DekuNutBundle_01": "TBox_DekuNutBundle",
    "Shop_DekuNutBundle_02": "TBox_DekuNutBundle",
    "Shop_DekuSeedBundle_01": "TBox_DekuSeedBundle",
    "Shop_DekuSeedBundle_02": "TBox_DekuSeedBundle",
    "Shop_DekuStick": "Item_DekuStickBundle_Small",
    "Shop_DekuStickBundle_Medium": "Item_DekuStickBundle_Small",
}
# Chest items that can't be sold: not pouch items, or chest-only bundle actors.
NOT_FOR_SALE_PREFIXES = ("TBox_", "Tbox_", "Item_Dungeon_", "Obj_MiniGame_")
NOT_FOR_SALE = {"Item_ScrubBusinessSeed"}


def survey():
    """Every shop slot (see the module notes), from the game's map files; cached in shops.json."""
    if SHOPS_JSON.exists():
        return json.loads(SHOPS_JSON.read_text(encoding="utf-8"))
    slots = {}
    for f in sorted((ROMFS / "Banc").rglob("*.bcett.byml.zs")):
        root = load_byml(f) or {}
        actors = root.get("Actors", []) or []
        by_hash = {a.get("Hash"): a for a in actors}
        rel = "Banc/" + f.relative_to(ROMFS / "Banc").as_posix()
        for a in actors:
            sale = [l["Dst"] for l in a.get("Links", []) or [] if l.get("Name") == "ForSale"]
            if not sale:
                continue
            keeper = a["Gyaml"]
            for suffix in ("_ShortStock", "_Child", "_Adult"):
                keeper = keeper.replace(suffix, "")
            if keeper in EXCLUDED_KEEPERS:
                continue
            area = rel.split("/")[1]
            for h in sale:
                s = by_hash[h]
                idx = (s.get("Dynamic") or {}).get("ShopItemIndex") or 0
                key = f"{area}/{keeper}/{idx}"
                slot = slots.setdefault(key, {"id": key, "area": area, "keeper": keeper, "index": idx,
                                              "item": s["Gyaml"], "copies": []})
                assert slot["item"] == s["Gyaml"], f"shop slot {key}: {slot['item']} vs {s['Gyaml']} in {rel}"
                slot["copies"].append([rel, h])
    out = sorted(slots.values(), key=lambda s: s["id"])
    SHOPS_JSON.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


_pouch_rows = None


def pouch_rows():
    global _pouch_rows
    if _pouch_rows is None:
        _pouch_rows = {r["__RowId"]: r for r in load_byml(ROMFS / POUCH_INFO)}
    return _pouch_rows


def shop_form(item):
    """The actor a shop shelf shows for item, or None when it can't be sold."""
    if item.startswith("Shop_"):
        return item
    if item.startswith(NOT_FOR_SALE_PREFIXES) or item in NOT_FOR_SALE or item not in pouch_rows():
        return None
    return RANDO_PREFIX + item


def chest_form(item):
    """The actor a chest gives for item, or None when it stays in shops."""
    if item.startswith("Shop_"):
        return CHEST_FORM.get(item)
    return item


def assign(slots, chest_free, pool, rng):
    """Spread pool (chest items and shop items) over the shop slots and the free chests.

    Items with no chest form go to shops first; the other shop slots take the next items that can be sold; what's
    left goes to the chests. Returns ({slot id: item}, [items for chest_free, in order])."""
    pool = list(pool)
    rng.shuffle(pool)
    shop_only = [i for i in pool if chest_form(i) is None]
    assert len(shop_only) <= len(slots), "more shop-only items than shop slots"
    rest = [i for i in pool if chest_form(i) is not None]
    order = list(slots)
    rng.shuffle(order)
    shop = dict(zip(order, shop_only))
    for sid in order[len(shop_only):]:
        k = next(n for n, i in enumerate(rest) if shop_form(i))
        shop[sid] = rest.pop(k)
    assert len(rest) == len(chest_free)
    return shop, rest


def shop_of(slot_id):
    return slot_id.rsplit("/", 1)[0]


def no_repeats(shop, fixed, rng):
    """shop ({slot id: item}) with no shop selling the same shelf actor twice, by swapping a repeat with a slot of
    another shop (slots in fixed stay). The game decides "sold out" by the actor's name: in seed 1206 the Kokiri shop
    had Shop_BombBundle_01 in places 1 and 6, place 1 is sold out before the Deku Tree, and place 6 was refused too
    ("Five bombs? Oh, wait..."). Returns the number of swaps."""
    swaps = 0
    def names(s):
        return [shops_item for sid, shops_item in ((k, shop_form(v)) for k, v in shop.items()) if shop_of(sid) == s]
    for _ in range(200):
        repeat = None
        for sid in sorted(shop):
            if sid not in fixed and names(shop_of(sid)).count(shop_form(shop[sid])) > 1:
                repeat = sid
                break
        if repeat is None:
            return swaps
        options = [o for o in sorted(shop) if o not in fixed and shop_of(o) != shop_of(repeat)
                   and shop_form(shop[o]) not in names(shop_of(repeat))
                   and shop_form(shop[repeat]) not in names(shop_of(o))]
        assert options, f"can't place {shop[repeat]} without a repeat"
        o = rng.choice(options)
        shop[repeat], shop[o] = shop[o], shop[repeat]
        swaps += 1
    raise AssertionError("shop repeats: no end")


# ---- new Shop_Rando_ actors ----
def _pack_files(name):
    raw = unzs((ROMFS / "Pack" / "Actor" / f"{name}.pack.zs").read_bytes())
    return parse_sarc(raw)


def _work(path):
    """A pack file name -> how other files name it ("Work/....gyml"), and back."""
    return "Work/" + path[:-len(".bgyml")] + ".gyml"


def _file(ref):
    return ref.lstrip("?").removeprefix("Work/").removesuffix(".gyml").removesuffix(".bgyml") + ".bgyml"


def make_shop_actor(item, price):
    """(pack bytes, ActorInfo row, GameActorInfo row, PouchActorInfo row) of Shop_Rando_<item>, selling item."""
    name = RANDO_PREFIX + item
    tpl = _pack_files(TEMPLATE)
    src = _pack_files(item)
    dump = lambda v: bymlw.dumps(v)
    load = lambda d: bymlw.loads(d)[0]
    own_param = f"Actor/{item}.engine__actor__ActorParam.bgyml"
    assert own_param in src, f"{item}: no ActorParam in its pack"
    comp = {}
    p = own_param
    while p in src:   # the item's components, its own over its parents' (inside its pack)
        a = load(src[p])
        for k, v in a.get("Components", {}).items():
            comp.setdefault(k, v)
        p = _file(a["$parent"]) if a.get("$parent") else None
    files = {}
    for path, data in tpl.items():   # the shelf parts: everything of the template but its tunic
        if "Armor" in path or "Obj_Item_Base" in path or path.startswith("GameParameter/"):
            continue
        files[path] = data
    for key in ("AnimationRef", "ModelInfoRef"):
        assert key in comp, f"{item}: no {key}"
        files[_file(comp[key])] = src[_file(comp[key])]
    pouch_ref = comp.get("PouchContentRef")
    assert pouch_ref and _file(pouch_ref) in src, f"{item}: no PouchContentParam in its pack"
    p = _file(pouch_ref)
    while p and p in src:   # the item's PouchContentParam and its parents, for the new one's $parent
        files[p] = src[p]
        parent = load(src[p]).get("$parent")
        p = _file(parent) if parent else None
    price_file = f"GameParameter/PriceParam/{name}.game__pouchcontent__PriceParam.bgyml"
    files[price_file] = dump({"BuyingPrice": price, "SellingPrice": 1})
    gpt_file = f"GameParameter/GameParameterTable/{name}.engine__actor__GameParameterTable.bgyml"
    files[gpt_file] = dump({"Category": "GameParameterTableDefault", "Components": {"PriceParam": "?" + price_file}})
    pouch_file = f"Component/PouchContentParam/{name}.game__component__PouchContentParam.bgyml"
    files[pouch_file] = dump({"$parent": _work(_file(pouch_ref)), "IsNotOpenStockNumDisplay": True,
                              "PouchActor": f"Work/Actor/{item}.engine__actor__ActorParam.gyml"})
    actor = {"$parent": "Work/Actor/Shop_Item_Base.engine__actor__ActorParam.gyml",
             "Components": {"ASInfoRef": "?Component/ASInfo/Item_Shop.engine__component__ASInfo.bgyml",
                            "ASRef": "?AS/Item_Common.root.asb",
                            "ActorNameRef": comp["ActorNameRef"],
                            "AnimationRef": comp["AnimationRef"],
                            "GameParameterTableRef": "?" + gpt_file,
                            "ModelInfoRef": comp["ModelInfoRef"],
                            "PouchContentRef": "?" + pouch_file}}
    files[f"Actor/{name}.engine__actor__ActorParam.bgyml"] = dump(actor)
    pack = bymlw.build_sarc(files, data_align={**bymlw.sarc_alignments(
        unzs((ROMFS / "Pack" / "Actor" / f"{TEMPLATE}.pack.zs").read_bytes()))})
    return pack


def add_rows(table, data, prices):
    """RSDB table data (BYML, the game's or already patched) with a Shop_Rando_<item> row for each item -> price of
    prices, kept sorted by __RowId. The rows copy the item's own (as the game's Shop_Mask_ rows copy Item_Mask_
    ones), with types kept (bymlw):
    - ActorInfo: the item's model row, under the new name; heap as the larger of the item's and the template shop
      actor's, plus 32 KB (a shelf actor has AI and physics an item actor lacks).
    - GameActorInfo: the template shop actor's row (rigid body layer of a shelf item).
    - PouchActorInfo: the item's row, with the price and no stock number shown."""
    import copy
    root, meta = bymlw.loads(data)
    rows = {r["__RowId"]: r for r in root}
    new = []
    for item, price in prices.items():
        name = RANDO_PREFIX + item
        assert name not in rows, f"row {name} already there"
        if table == ACTOR_INFO:
            r = copy.deepcopy(rows[item])
            heap = max(int(r.get("InstanceHeapSize", 0)), int(rows[TEMPLATE]["InstanceHeapSize"])) + 0x8000
            r["InstanceHeapSize"] = type(rows[TEMPLATE]["InstanceHeapSize"])(heap)
        elif table == GAME_ACTOR_INFO:
            r = copy.deepcopy(rows[TEMPLATE])
        else:
            r = copy.deepcopy(rows[item])
            r["BuyingPrice"] = type(rows[TEMPLATE].get("BuyingPrice", 0))(price)
            r["IsNotOpenStockNumDisplay"] = True
        r["__RowId"] = name
        new.append(r)
    root = sorted(root + new, key=lambda r: r["__RowId"].encode())
    return bymlw.dumps(root, meta["endian"], meta["version"])


# ---- bombs for sale before the Goron's Ruby ----
# DisplayShop (every display shop's buying flow) checks the selected item's kind before a sale (event 284,
# EventQueryCheckShopSelectItemName): seeds need IsGet.Item_BulletBag_Small, arrows IsGet.Item_Quiver_Small, bottle
# contents an empty bottle, and bombs Get_StoneOfFire_IsCompleted_Exp (event 187): until the Goron's Ruby every shop
# answers "The bombs it is! Oh, wait. Actually..." (Npc_Kokiri_001_Shop_Child:Shop_01_02_2, found 2026-11-09, seed
# 1207). Its second check (event 327, OutOfStockCheck) gives the Goron and bazaar shops' "no bombs until Goron City
# is sorted" talk. With shop shuffle bombs can be in any shop at any time, so both checks pass either way: their
# "not yet" case goes where "yes" goes. The pouch check after them still refuses bombs with no room for them.
# (Switch cases: 0 is true, 1 false, as the bag checks next to them show.)
DISPLAY_SHOP = "Event/EventFlow/DisplayShop.bfevfl.zs"
BOMB_GATES = ((187, "Get_StoneOfFire_IsCompleted_Exp", [(0, 172), (1, 188)]),
              (327, "Get_StoneOfFire_IsCompleted_Exp", [(0, 71), (1, 24)]))


def unlock_bombs(data):
    """DisplayShop data with the Goron's Ruby checks on bombs always passing (two u16 case targets, in place)."""
    import struct
    from evfl import Flow
    f = Flow(data)
    out = bytearray(data)
    for i, flag, cases in BOMB_GATES:
        e = f.event(i)
        assert e["type"] == "switch" and f.params(e["params"]).get("GameDataName") == flag and \
            [(v, n) for v, n, _ in e["cases"]] == cases, f"DisplayShop changed: event {i}"
        struct.pack_into("<H", out, e["cases"][1][2], cases[0][1])
    return bytes(out)


# ---- sold out ----
# A one-of-a-kind chest item on a shelf leaves it once owned: the placed shop actor's Presence (the one-flag condition
# a map actor shows on: FlagName, IsNegation, IsCheckLazy) becomes "not IsGet.<item>". The game sets IsGet.<item> when
# the item reaches the pouch, from a shop or anywhere else (so a Hookshot already found leaves the shelf too; the
# few items that exist twice, such as the Silver Gauntlets, aren't needed twice).
# Shelf copies that already have a Presence (Kokiri Forest's before and after the Deku Tree, the Market's bug) get
# a new BoolExp game-data entry: (not IsGet.<item>) and (their own flag, negated if it was). BoolExp entries are
# stack programs, found from the game's own (2026-11-09): [0, bool hash] pushes a Bool, [10, hash] a BoolExp,
# [5] is not, [3] and. They aren't saved (SaveFileIndex -1), so adding some leaves the save layout as it is.
# Heart Pieces stay buyable: there are several, and one owned sets IsGet for all.
NOT_SOLD_OUT = {"Obj_Heart_Piece"}
OP_BOOL, OP_BOOLEXP, OP_NOT, OP_AND = 0, 10, 5, 3


def sold_out_flag(item, bools):
    """The IsGet flag that takes item off its shelf, or None (shop items, Heart Pieces, items without the flag)."""
    if item.startswith("Shop_") or item in NOT_SOLD_OUT:
        return None
    flag = f"IsGet.{item}"
    from gamedata import murmur3
    return flag if murmur3(flag.encode()) in bools else None


def presence_for(old, item, bools, exps):
    """(new Presence or None for no change, {BoolExp name: values} it needs) of a shelf copy of item whose Presence
    was old. bools / exps: the hashes of the game's Bool / BoolExp entries."""
    from gamedata import murmur3
    flag = sold_out_flag(item, bools)
    if flag is None:
        return None, {}
    if not old:
        return {"FlagName": flag, "IsCheckLazy": True, "IsNegation": True}, {}
    own = old["FlagName"]
    h = murmur3(own.encode())
    assert h in bools or h in exps, f"shelf Presence flag {own} isn't a Bool or BoolExp"
    name = f"Rando_ShopShelf_{item}_{'Not' if old.get('IsNegation') else ''}{own}"
    values = [[OP_BOOL, murmur3(flag.encode())], [OP_NOT], [OP_BOOL if h in bools else OP_BOOLEXP, h]]
    if old.get("IsNegation"):
        values.append([OP_NOT])
    values.append([OP_AND])
    return {"FlagName": name, "IsCheckLazy": old.get("IsCheckLazy", True), "IsNegation": False}, {name: values}


def add_bool_exps(data, exps):
    """GameDataList data (BYML) with BoolExp entries name -> values appended, typed as the game's own."""
    from gamedata import murmur3
    root, meta = bymlw.loads(data)
    table = root["Data"]["BoolExp"]
    have = {int(e["Hash"]) for e in table}
    sample = table[0]
    hash_type = type(sample["Hash"])
    op_type = arg_type = bymlw.U64   # (the game's: ops and flag hashes are u64; small enum constants are s32)
    for name, values in sorted(exps.items()):
        h = murmur3(name.encode())
        assert h not in have, f"BoolExp {name}: hash already used"
        table.append({"Hash": hash_type(h), "ResetTypeValue": type(sample["ResetTypeValue"])(0),
                      "SaveFileIndex": type(sample["SaveFileIndex"])(-1),
                      "Values": [[op_type(v[0])] + [arg_type(x) for x in v[1:]] for v in values]})
    return bymlw.dumps(root, meta["endian"], meta["version"])


def set_presences(data, presences):
    """Map data with each actor Hash -> Presence dict of presences set (a full rewrite, bymlw)."""
    root, meta = bymlw.loads(data)
    left = dict(presences)
    for a in root["Actors"]:
        h = int(a.get("Hash", -1))
        if h in left:
            a["Presence"] = left.pop(h)
    assert not left, f"actors not found: {sorted(left)}"
    return bymlw.dumps(root, meta["endian"], meta["version"])


def price_for(item, rng):
    return rng.randrange(1, 10) * 10   # 10 to 90: a child's wallet holds 99
