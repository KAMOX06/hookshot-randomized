"""Dungeon items (small keys, boss keys, maps, compasses) on shop shelves, counting for their own dungeon.

A shop has nothing that tells one dungeon's key from another's, and the pickup credits the dungeon the player is in
(none, in a shop). So each dungeon item on a shelf is an item of its own:
- a shelf actor Shop_Rando_DungeonItem_<NN> (shops.make_shop_actor: sells Item_Dungeon_<X>), whose ActorInfo row
  names it Item_Dungeon_<X>_<Dungeon>_<NN>: the name the shop knows the selected item by (Shop_SelectItemName, as
  DisplayShop's own checks compare it: 'Weapon_Shield_Deku', 'Armor_Adult_Zora') and its messages' labels;
- messages in ActorMsg/PouchContent.msbt, every language: _Name ("Forest Temple Small Key"), _GetMessage (for a key,
  with the dungeon's key count, as the Gold Skulltula count is shown), _GetCaption (the item's own);
- a saved Bool Rando_ShopSold_<NN> (a new GameDataList entry: a new flag is saved and read back, seed 1228), set when
  it's bought: the shelf copies' Presence hides the item once it's on (shops.presence_for), so it's bought once;
- DisplayShop (every shop's buying flow): on each buying route, before the item got is shown (PAY_FIRST,
  GET_BEFORE_PAY), a chain, one link per such item: EventQueryIsGameDataString64 Shop_SelectItemName == its name -> set its sold flag,
  then credit its dungeon (SetGameDataBool DungeonInfo.<D>.IsGetMap etc., or AddOrSubGameDataInt
  DungeonInfo.<D>.SmallKeyNum +1, an action added to GameDataActor there) -> the next link; the last link goes where
  the pay step went (evfledit.py).
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PAY_EVENTS = (138, 139, 140, 288)
# The buying routes (the item's first purchase from a shop or not, bottles): 138 and 139 pay, then show the item got
# (ShopItem_Buy_SecondMove, in a fork after); 140 and 288 come after the item got is shown (293 ShopGet -> 140;
# 22 ShopGet, 299 ShopGet_SoldOut -> 288; 22 and 299 are the results of the ShopCheck call, 310). A key's message
# reads its dungeon's count, so the credit goes before that message: after 138 / 139, before 22 / 299 / 293.
PAY_FIRST = (138, 139)
GET_BEFORE_PAY = {22: "ShopGet", 299: "ShopGet_SoldOut", 293: "ShopGet"}
NAME_QUERY_TEMPLATE = 230   # EventQueryIsGameDataString64 Shop_SelectItemName == 'Weapon_Shield_Deku'
SET_BOOL_TEMPLATE = 485     # EventTriggerSetGameDataBool (Index, Value, GameDataName)
POUCH_MSG = "ActorMsg/PouchContent.msbt"
SHELF_PREFIX = "Shop_Rando_DungeonItem_"
SOLD_PREFIX = "Rando_ShopSold_"
KIND_WORDS = {"Item_Dungeon_SmallKey": "Small Key", "Item_Dungeon_Map": "Map", "Item_Dungeon_Compass": "Compass",
              "Item_Dungeon_BossKey": "Boss Key"}


def short_name(dungeon):
    import keycredit
    n = keycredit.NAMES.get(dungeon, dungeon)
    return n[4:] if n.startswith("the ") else n


class Instance:
    """One dungeon item on a shelf: its item, dungeon, number."""

    def __init__(self, item, dungeon, n):
        self.item, self.dungeon, self.n = item, dungeon, n

    @property
    def shelf(self):
        return f"{SHELF_PREFIX}{self.n:02d}"

    @property
    def name(self):
        return f"{self.item}_{self.dungeon}_{self.n:02d}"

    @property
    def sold_flag(self):
        return f"{SOLD_PREFIX}{self.n:02d}"

    def credit(self):
        """(action, GameData name, value) the purchase runs for its dungeon."""
        import dungeoncredit
        if self.item == "Item_Dungeon_SmallKey":
            return "EventTriggerAddOrSubGameDataInt", dungeoncredit.key_flag(self.dungeon), 1
        return "EventTriggerSetGameDataBool", dungeoncredit.flag_for(self.item, self.dungeon), True

    def messages(self, captions):
        """{label: bytes} of its messages; captions: {item: the item's own caption bytes}."""
        import keycredit
        import msbt
        long = keycredit.NAMES.get(self.dungeon, self.dungeon)
        word = KIND_WORDS[self.item]
        out = {f"{self.name}_Name": f"{short_name(self.dungeon)} {word}".encode()}
        if self.item == "Item_Dungeon_SmallKey":
            out[f"{self.name}_GetMessage"] = (f"You got a small key for {long}!\nKeys for {long}: ".encode() +
                                              msbt.gamedata_int_tag(f"DungeonInfo.{self.dungeon}.SmallKeyNum"))
        else:
            out[f"{self.name}_GetMessage"] = f"You got the {word.lower()} of {long}!".encode()
        if captions.get(self.item):
            out[f"{self.name}_GetCaption"] = captions[self.item]
        return out


def add_purchase_chain(data, instances):
    """DisplayShop data with the purchase chains (see the module notes)."""
    from evfledit import FlowEditor
    ed = FlowEditor(data)
    f = ed.f
    acts = f.actors()
    for ev in PAY_EVENTS:
        e = f.event(ev)
        a = acts[e["actor"]]
        assert e["type"] == "action" and a["actions"][e["action"]] == "EventTriggerPayItemPriceAtShop", f"event {ev}"
    q = f.event(NAME_QUERY_TEMPLATE)
    assert acts[q["actor"]]["queries"][q["query"]] == "EventQueryIsGameDataString64" and \
        f.params(q["params"]).get("GameDataName") == "Shop_SelectItemName", "name query template"
    s = f.event(SET_BOOL_TEMPLATE)
    assert acts[s["actor"]]["actions"][s["action"]] == "EventTriggerSetGameDataBool", "set bool template"
    for ev, name in GET_BEFORE_PAY.items():
        e = f.event(ev)
        assert e["type"] == "subflow" and f.s(struct.unpack_from("<Q", data, e["off"] + 0x20)[0]) == name, f"event {ev}"
    gda = ed.actor_index("GameDataActor")
    query = acts[gda]["queries"].index("EventQueryIsGameDataString64")
    set_bool = ed.action_index(gda, "EventTriggerSetGameDataBool")
    add_int = ed.action_index(gda, "EventTriggerAddOrSubGameDataInt", add=True)

    def chain(nxt):
        for inst in reversed(instances):   # (built back to front: each link goes on to the next one)
            action, flag, value = inst.credit()
            if action == "EventTriggerAddOrSubGameDataInt":
                p = ed.params_with_types(SET_BOOL_TEMPLATE, {"GameDataName": flag, "Value": value}, {"Value": 2})
                credit = ed.add_action(gda, add_int, p, nxt)
            else:
                p = ed.params_like(SET_BOOL_TEMPLATE, {"GameDataName": flag, "Value": True})
                credit = ed.add_action(gda, set_bool, p, nxt)
            p = ed.params_like(SET_BOOL_TEMPLATE, {"GameDataName": inst.sold_flag, "Value": True})
            sold = ed.add_action(gda, set_bool, p, credit)
            p = ed.params_like(NAME_QUERY_TEMPLATE, {"GameDataName": "Shop_SelectItemName", "Value": inst.name})
            nxt = ed.add_switch(gda, query, p, [(0, sold), (1, nxt)])
        return nxt

    for ev in PAY_FIRST:   # (the get message after the payment: credited right after it)
        ed.set_next(ev, chain(ed.next_of(ev)))
    for ev in GET_BEFORE_PAY:   # (the get message, then the payment: credited before the message, so it counts)
        n = ed.redirect(ev, chain(ev))
        assert n, f"nothing leads to event {ev}"
    return ed.build()


def add_sold_flags(data, instances):
    """GameDataList data with a saved Bool per instance (default off), shaped as IsGet.Item_Boomerang's entry."""
    import bymlw
    from gamedata import murmur3
    root, meta = bymlw.loads(data)
    bools = root["Data"]["Bool"]
    tpl = next(e for e in bools if int(e["Hash"]) == murmur3(b"IsGet.Item_Boomerang"))
    have = {int(e["Hash"]) for e in bools}
    for inst in instances:
        h = murmur3(inst.sold_flag.encode())
        assert h not in have, f"{inst.sold_flag}: hash taken"
        e = dict(tpl)
        e["Hash"], e["DefaultValue"] = type(tpl["Hash"])(h), False
        bools.append(e)
    return bymlw.dumps(root, meta["endian"], meta["version"])


def item_captions(sarc_data):
    """{item: its _GetCaption message} from a language archive's PouchContent.msbt."""
    import msbt
    from nxfmt import parse_sarc
    labels, msgs, _, _ = msbt.read(parse_sarc(sarc_data)[POUCH_MSG])
    return {i: msgs[labels[f"{i}_GetCaption"]][:-1] for i in KIND_WORDS if f"{i}_GetCaption" in labels}


def add_messages(sarc_data, instances, extra=None):
    """A language archive with each instance's messages in PouchContent.msbt (and extra: {msbt path: {label: bytes}})."""
    import bymlw
    import msbt
    from nxfmt import parse_sarc
    files = parse_sarc(sarc_data)
    caps = item_captions(sarc_data)
    new = {}
    for inst in instances:
        new.update(inst.messages(caps))
    if new:
        files[POUCH_MSG] = msbt.add_messages(files[POUCH_MSG], new)
    for path, msgs in (extra or {}).items():
        files[path] = msbt.add_messages(files[path], msgs)
    return bymlw.build_sarc(files, data_align=bymlw.sarc_alignments(sarc_data))
