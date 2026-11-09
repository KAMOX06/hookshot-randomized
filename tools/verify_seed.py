"""Check a randomizer overlay against the game's files and its spoiler log.

Map files: same actors as the original, the only differences being chests' Drop__DropActor, each as the spoiler says.
Every copy of a chest holds the same item, the items as a whole are unchanged, and each dungeon keeps its own
dungeon items. GameDataList: only the flags of seed.json differ, each holding exactly 0 or 1. Size table: only the
grown files' entries differ, by their growth.
"""
import binascii
import collections
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from nxfmt import ROMFS, load_byml, unzs
from gamedata import GDL, murmur3

overlay = Path(sys.argv[1])
problems = []
spoiler = {}   # chest hash -> (area, actor the chest gives)
pool_item = {}   # chest hash -> the item as the shuffle placed it (a shop item's Shop_ name when it came from a shop)
for line in (overlay / "spoiler.txt").read_text(encoding="utf-8").splitlines()[3:]:
    if not line.strip():
        break   # (the shop list follows)
    p = line.split()
    spoiler[int(p[2])] = (p[0], p[3])
    pool_item[int(p[2])] = p[6].rstrip(")") if len(p) > 6 and p[4:6] == ["(shop", "item"] else p[3]

import shops
seedinfo = json.loads((overlay / "seed.json").read_text(encoding="utf-8"))
shop = seedinfo.get("shop", {})
prices = seedinfo.get("shop_prices", {})
slots = {s["id"]: s for s in shops.survey()} if shop else {}
shelf, shelf_was, shelf_item = {}, {}, {}   # (map file, placed shop item hash) -> its Shop_ actor now / before / item
for sid, item in shop.items():
    for rel, h in slots[sid]["copies"]:
        shelf[(rel, h)] = shops.shop_form(item)
        shelf_was[(rel, h)] = slots[sid]["item"]
        shelf_item[(rel, h)] = item
    if shops.shop_form(item) is None:
        problems.append(f"shop slot {sid}: {item} can't be sold")
ds = Path(shops.DISPLAY_SHOP)
if shop:
    from evfl import Flow
    a, b = unzs((ROMFS / ds).read_bytes()), unzs((overlay / ds).read_bytes()) if (overlay / ds).exists() else b""
    fa = Flow(a)
    allowed = {fa.event(i)["cases"][1][2] + k for i, _, _ in shops.BOMB_GATES for k in (0, 1)}
    diff = [i for i in range(len(a)) if i >= len(b) or a[i] != b[i]]
    fb = Flow(b) if b else None
    if len(a) != len(b) or any(i not in allowed for i in diff) or \
            any(fb.event(i)["cases"][1][1] != cases[0][1] for i, _, cases in shops.BOMB_GATES):
        problems.append(f"DisplayShop: unexpected edit ({len(diff)} bytes differ)")
    else:
        print(f"DisplayShop: the 2 Goron's Ruby checks on bombs pass, {len(diff)} byte(s) differ")
elif (overlay / ds).exists():
    problems.append("DisplayShop in the overlay without shop shuffle")
by_shop = collections.Counter((shops.shop_of(sid), shops.shop_form(item)) for sid, item in shop.items())
problems += [f"shop {s} sells {n} twice" for (s, n), c in by_shop.items() if c > 1]
import randomize
gd_bools, gd_exps = randomize.gd_hashes("Bool"), randomize.gd_hashes("BoolExp")
want_exps = {}   # BoolExp entries the sold-out shelves need
for h, item in pool_item.items():
    if shops.chest_form(item) != spoiler[h][1]:
        problems.append(f"chest {h}: spoiler gives {spoiler[h][1]} for {item}")

dropped_events = seedinfo.get("dropped_events", {})
seen = collections.defaultdict(set)
growth = {}
files = sorted(overlay.rglob("*.bcett.byml.zs"))
for f in files:
    rel = f.relative_to(overlay).as_posix()
    old, new = load_byml(ROMFS / rel)["Actors"], load_byml(f)["Actors"]
    growth[rel[:-3]] = len(unzs(f.read_bytes())) - len(unzs((ROMFS / rel).read_bytes()))
    if len(old) != len(new):
        problems.append(f"{rel}: actor count changed")
        continue
    for o, n in zip(old, new):
        key = (rel, o.get("Hash"))
        want_kind = shelf.pop(key, o.get("Gyaml"))
        want_pres = o.get("Presence")
        if key in shelf_item:
            p, need = shops.presence_for(o.get("Presence"), shelf_item[key], gd_bools, gd_exps)
            if p is not None:
                want_pres = p
                want_exps.update(need)
        if o == n and want_kind == o.get("Gyaml") and want_pres == o.get("Presence"):
            continue
        if key in shelf_item or o.get("Gyaml", "").startswith("Shop_"):
            rest = lambda a: {k: v for k, v in a.items() if k not in ("Gyaml", "Presence")}
            if n.get("Gyaml") != want_kind or n.get("Presence") != want_pres or rest(o) != rest(n):
                problems.append(f"{rel}: shop item {o.get('Hash')} is {n.get('Gyaml')} {n.get('Presence')}, expected "
                                f"{want_kind} {want_pres} (and nothing else changed)")
            continue
        od, nd = dict(o.get("Dynamic") or {}), dict(n.get("Dynamic") or {})
        od.pop("Drop__DropActor", None), nd.pop("Drop__DropActor", None)
        if str(o.get("Hash")) in dropped_events:
            if od.get("GetEventName") != dropped_events[str(o["Hash"])] or "GetEventName" in nd:
                problems.append(f"{rel}: {o['Hash']} event not removed as seed.json says")
            od.pop("GetEventName", None)
        if od != nd or {k: v for k, v in o.items() if k != "Dynamic"} != {k: v for k, v in n.items() if k != "Dynamic"}:
            problems.append(f"{rel}: {o.get('Hash')} changed beyond its item")
        h, item = n["Hash"], n["Dynamic"]["Drop__DropActor"]
        if spoiler.get(h, (None, None))[1] != item:
            problems.append(f"{rel}: {h} holds {item}, spoiler says {spoiler.get(h)}")
        seen[h].add(item)
problems += [f"chest {h}: copies disagree {v}" for h, v in seen.items() if len(v) > 1]
problems += [f"{rel}: shop item {h} should be {k}, but its map file isn't in the overlay"
             for (rel, h), k in shelf.items() if k != shelf_was[(rel, h)]]

chests = json.loads((Path(__file__).parent / "chests.json").read_text(encoding="utf-8"))
first = {}
for c in chests:
    first.setdefault(c["hash"], c)
orig = collections.Counter([first[h]["drop"] for h in spoiler] + [s["item"] for s in slots.values()])
now = collections.Counter(list(pool_item.values()) + list(shop.values()))
if orig != now:
    problems.append(f"item totals differ: {orig - now} lost, {now - orig} extra")
was = collections.Counter(first[h]["file"].split("\\")[0] for h in spoiler if first[h]["drop"].startswith("Item_Dungeon_"))
is_ = collections.Counter(area for area, item in spoiler.values() if item.startswith("Item_Dungeon_"))
if was != is_:
    problems.append(f"dungeon items per dungeon changed: {was} -> {is_}")

flags = json.loads((overlay / "seed.json").read_text(encoding="utf-8"))["flags"]
gdl_ov = overlay / GDL.relative_to(ROMFS)
if flags or gdl_ov.exists():
    want = {murmur3(k.encode()): (k, v) for k, v in flags.items()}
    old_d, new_d = load_byml(GDL)["Data"], load_byml(gdl_ov)["Data"]   # load_byml rejects bools other than 0/1
    for kind in old_d:
        for o, n in zip(old_d[kind], new_d[kind]):
            if o == n:
                continue
            name, value = want.get(o.get("Hash"), (o.get("Hash"), None))
            right_kind = kind == ("Bool" if isinstance(value, bool) else "Int")
            if not right_kind or {k for k in o if o[k] != n.get(k)} != {"DefaultValue"} or n["DefaultValue"] != value:
                problems.append(f"game data: unexpected change to {name}")
            else:
                print(f"flag {name}: {o['DefaultValue']} -> {n['DefaultValue']}")
            want.pop(o.get("Hash"), None)
    problems += [f"flag {n} not changed" for n, v in want.values() if v is not None and
                 next(e for e in old_d["Bool" if isinstance(v, bool) else "Int"]
                      if e["Hash"] == murmur3(n.encode()))["DefaultValue"] != v]
    for kind in old_d:   # entries added: only the sold-out shelves' BoolExp conditions
        extra = new_d[kind][len(old_d[kind]):]
        if kind != "BoolExp" and extra:
            problems.append(f"game data: {len(extra)} {kind} entries added")
    added = {e["Hash"]: e for e in new_d["BoolExp"][len(old_d["BoolExp"]):]}
    expect_exps = {murmur3(n.encode()): v for n, v in want_exps.items()}
    if set(added) != set(expect_exps) or any(added[h]["Values"] != v or added[h]["SaveFileIndex"] != -1
                                             for h, v in expect_exps.items() if h in added):
        problems.append(f"game data: BoolExp added {len(added)}, expected {len(expect_exps)} sold-out conditions")
    elif added:
        print(f"game data: {len(added)} sold-out BoolExp conditions added")
    gsize = len(unzs(gdl_ov.read_bytes())) - len(unzs(GDL.read_bytes()))
    if gsize:
        growth[GDL.relative_to(ROMFS).as_posix()[:-3]] = gsize
elif want_exps:
    problems.append("sold-out shelves need BoolExp conditions, but GameDataList isn't in the overlay")

door = Path("Logic/Market_Common_f252.logic.module.ainb")
if (overlay / door).exists():
    a, b = (ROMFS / door).read_bytes(), (overlay / door).read_bytes()
    multi = struct.unpack_from("<I", a, 0x38)[0]
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    if len(a) != len(b) or struct.unpack_from("<hh", b, multi + 8) != (6, 0) or any(not multi + 8 <= i < multi + 12 for i in diff):
        problems.append(f"door logic: unexpected edit ({len(diff)} bytes differ)")
    else:
        print(f"door logic: OR source 1 -> flag node, {len(diff)} byte(s) differ")
elif json.loads((overlay / "seed.json").read_text(encoding="utf-8"))["flags"].get("Market_Child_TimeTempleDoor_Opened"):
    problems.append("door flag set but the door logic is not rewired")
sword = Path("Logic/Market_Child_Prefab_e700.logic.module.ainb")
if (overlay / door).exists() != (overlay / sword).exists():
    problems.append("door and Master Sword logic patches out of step")
elif (overlay / sword).exists():
    a, b = (ROMFS / sword).read_bytes(), (overlay / sword).read_bytes()
    multi = struct.unpack_from("<I", a, 0x38)[0]
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    if len(a) != len(b) or struct.unpack_from("<hh", b, multi) != (2, 1) or any(not multi <= i < multi + 4 for i in diff):
        problems.append(f"sword logic: unexpected edit ({len(diff)} bytes differ)")
    else:
        print(f"sword logic: first pull -> DoorOfTime IsBefore Step06, {len(diff)} byte(s) differ")

if json.loads((overlay / "seed.json").read_text(encoding="utf-8")).get("cutscene_skip"):
    from evfl import Flow
    house, bridge = Path("Event/EventFlow/Dm_Kokiri_0045.bfevfl.zs"), Path("Event/EventFlow/Dm_Kokiri_0150.bfevfl.zs")
    a, b = unzs((ROMFS / house).read_bytes()), unzs((overlay / house).read_bytes())
    fb = Flow(b)
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    if len(a) != len(b) or fb.event(51)["next"] != 45 or fb.event(45)["next"] != 36 or len(diff) > 4:
        problems.append(f"Saria house: unexpected edit ({len(diff)} bytes differ)")
    else:
        print(f"Saria house: 51 -> 45 -> 36, {len(diff)} byte(s) differ")
    a, b = unzs((ROMFS / bridge).read_bytes()), unzs((overlay / bridge).read_bytes())
    t = struct.unpack_from("<Q", b, struct.unpack_from("<Q", b, 0x38)[0])[0]
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    if len(a) != len(b) or struct.unpack_from("<f", b, t + 0x10)[0] != 1.0 or any(not t + 0x10 <= i < t + 0x14 for i in diff):
        problems.append(f"Saria bridge: unexpected edit ({len(diff)} bytes differ)")
    else:
        print(f"Saria bridge: Dm_Kokiri_0150 {struct.unpack_from('<f', a, t + 0x10)[0]:.0f} -> 1 frame")
    import cutscenes
    for n in cutscenes.AREA_INTROS + cutscenes.KAEPORA_TALKS:
        rel = Path(f"Event/EventFlow/{n}.bfevfl.zs")
        a, b = unzs((ROMFS / rel).read_bytes()), unzs((overlay / rel).read_bytes())
        fa = Flow(a)
        links = set()
        for e in fa.events():
            if "next_field" in e:
                links |= {e["next_field"], e["next_field"] + 1}
        for _, pos in fa.entry_points().values():
            links |= {pos, pos + 1}
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        if len(a) != len(b) or not diff or any(i not in links for i in diff):
            problems.append(f"{n}: unexpected edit ({len(diff)} bytes differ)")
    for n in cutscenes.KAEPORA_TIMELINES:
        rel = Path(f"Event/EventFlow/{n}.bfevfl.zs")
        a, b = unzs((ROMFS / rel).read_bytes()), unzs((overlay / rel).read_bytes())
        t = struct.unpack_from("<Q", b, struct.unpack_from("<Q", b, 0x38)[0])[0]
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        if len(a) != len(b) or struct.unpack_from("<f", b, t + 0x10)[0] != 1.0 or any(not t + 0x10 <= i < t + 0x14 for i in diff):
            problems.append(f"{n}: unexpected edit")
    print(f"area intros and Kaepora: {len(cutscenes.AREA_INTROS + cutscenes.KAEPORA_TALKS)} made instant, "
          f"{len(cutscenes.KAEPORA_TIMELINES)} cut (only links / length changed)")

bomb = Path("Pack/Actor/Item_Bomb.pack.zs")
if not json.loads((overlay / "seed.json").read_text(encoding="utf-8")).get("bombs_need_bracelet", True):
    from nxfmt import Byml, parse_sarc
    lift = "Component/LiftableParam/Item_Bomb.game__component__LiftableParam.bgyml"
    a, b = unzs((ROMFS / bomb).read_bytes()), unzs((overlay / bomb).read_bytes())
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    if len(a) != len(b) or len(diff) > 4 or Byml(parse_sarc(b)[lift]).root["LiftGrade"] != 3069107721:
        problems.append(f"bombs: unexpected edit ({len(diff)} bytes differ)")
    else:
        print(f"bombs: LiftGrade lowered to the pots' grade, {len(diff)} byte(s) differ")
elif (overlay / bomb).exists():
    problems.append("Item_Bomb.pack in the overlay although bombs keep needing the bracelet")

seedinfo = json.loads((overlay / "seed.json").read_text(encoding="utf-8"))
if not seedinfo.get("free_magic", True):
    import itempatches
    from nxfmt import Byml, parse_sarc
    want = itempatches.magic_packs()
    if sorted(seedinfo.get("magic_packs", [])) != sorted(want):
        problems.append("magic: the patched packs differ from the ones that give magic")
    for rel in want:
        a, b = unzs((ROMFS / rel).read_bytes()), unzs((overlay / rel).read_bytes())
        sa, sb = parse_sarc(a), parse_sarc(b)
        bad = len(a) != len(b) or sa.keys() != sb.keys()
        for name in sa:
            if sa[name] == sb.get(name):
                continue
            if "GameParameterTable/" not in name:
                bad = True
                continue
            ca, cb = Byml(sa[name]).root["Components"], Byml(sb[name]).root["Components"]
            bad |= "ExpansionAbilityActor" in cb or {k: v for k, v in ca.items() if k != "ExpansionAbilityActor"} != cb
        if bad:
            problems.append(f"magic: {rel}: unexpected edit")
    print(f"magic: {len(want)} item packs without magic (Great Fairies keep theirs)")
elif seedinfo.get("magic_packs"):
    problems.append("magic packs in the overlay although magic items keep giving magic")

# PouchActorInfo: the game's rows as expected after the magic and bag options (shop rows: checked below)
import itempatches
from nxfmt import Byml
info = Path(itempatches.POUCH_INFO)
no_magic, bag = not seedinfo.get("free_magic", True), seedinfo.get("bombs_give_bag", False)
if no_magic or bag or prices:
    a, b = unzs((ROMFS / info).read_bytes()), unzs((overlay / info).read_bytes()) if (overlay / info).exists() else b""
    ra = {r["__RowId"]: r for r in Byml(a).root}
    rb = {r["__RowId"]: r for r in Byml(b).root} if b else {}
    gone = [n for n, r in ra.items() if r.get("ExpansionAvility") == "MPMaxUP" and "FairyBless" not in n] if no_magic else []
    rb = {n: r for n, r in rb.items() if not n.startswith(shops.RANDO_PREFIX)}
    bad = ra.keys() != rb.keys()
    for n, r in ra.items():
        if bad:
            break
        want_row = {k: v for k, v in r.items() if k not in itempatches.RSDB_MAGIC_KEYS} if n in gone else dict(r)
        if bag and n in randomize.BOMB_ROWS:
            want_row["IsAbleToGetBagIfGet"] = True
        bad = rb[n] != want_row
    if b:
        growth[info.as_posix()[:-3]] = len(b) - len(a)
    if bad or (no_magic and not gone):
        problems.append("PouchActorInfo: unexpected edit, or the magic / bag rows not patched")
    else:
        print(f"item table: {len(gone)} magic rows without magic" + (", bombs give the Bomb Bag" if bag else ""))
elif (overlay / info).exists():
    problems.append("item table in the overlay although no option changes it")

new_sizes = {}
if prices:   # chest items on shelves: their Shop_Rando_ actors and table rows
    from nxfmt import Byml, parse_sarc
    sold = {i for i in shop.values() if not i.startswith("Shop_")}
    if set(prices) != sold:
        problems.append(f"shops: prices for {sorted(set(prices))} but sold {sorted(sold)}")
    for item in sorted(sold):
        name = shops.RANDO_PREFIX + item
        rel = f"Pack/Actor/{name}.pack.zs"
        if not (overlay / rel).exists():
            problems.append(f"shops: {rel} missing")
            continue
        raw = unzs((overlay / rel).read_bytes())
        new_sizes[rel[:-3]] = raw
        pack = parse_sarc(raw)
        actor = Byml(pack[f"Actor/{name}.engine__actor__ActorParam.bgyml"]).root
        pc = Byml(pack[actor["Components"]["PouchContentRef"].lstrip("?")]).root
        pp = Byml(pack[f"GameParameter/PriceParam/{name}.game__pouchcontent__PriceParam.bgyml"]).root
        if pc.get("PouchActor") != f"Work/Actor/{item}.engine__actor__ActorParam.gyml" or \
                pp["BuyingPrice"] != prices[item]:
            problems.append(f"shops: {name} doesn't sell {item} for {prices[item]}")
        for ref in actor["Components"].values():   # pack-local refs must be in the pack
            f = ref.lstrip("?")
            if f.endswith(".bgyml") and not f.startswith(("AS/", "ActorSystem/")) and f not in pack:
                problems.append(f"shops: {name}: {f} not in its pack")
    for table in (shops.ACTOR_INFO, shops.GAME_ACTOR_INFO, shops.POUCH_INFO):
        ov = overlay / table
        if not ov.exists():
            problems.append(f"shops: {table} missing")
            continue
        ra = {r["__RowId"]: r for r in load_byml(ROMFS / table)}
        rb = {r["__RowId"]: r for r in load_byml(ov)}
        ids = [r["__RowId"] for r in load_byml(ov)]
        extra = {n for n in rb if n not in ra}
        if extra != {shops.RANDO_PREFIX + i for i in sold} or ids != sorted(ids):
            problems.append(f"shops: {table}: rows added {sorted(extra)[:5]}..., sorted {ids == sorted(ids)}")
        if table != shops.POUCH_INFO and any(rb[n] != r for n, r in ra.items()):
            problems.append(f"shops: {table}: a game row changed")
        if table == shops.POUCH_INFO:
            for i in sold:
                r = rb.get(shops.RANDO_PREFIX + i, {})
                if r.get("BuyingPrice") != prices[i]:
                    problems.append(f"shops: PouchActorInfo price of {i}: {r.get('BuyingPrice')}")
        growth[table[:-3]] = len(unzs(ov.read_bytes())) - len(unzs((ROMFS / table).read_bytes()))
    print(f"shops: {sum(1 for s, i in shop.items() if i != slots[s]['item'])} of {len(shop)} slots changed, "
          f"{len(sold)} chest items sold by new shop actors (rows in the 3 item tables)")
elif (overlay / shops.ACTOR_INFO).exists():
    problems.append("shop tables in the overlay without shop shuffle")

import randomize
start = Path(randomize.GAME_START)
if seedinfo.get("start_deku_nut"):
    import ainbedit
    a, b = (ROMFS / start).read_bytes(), (overlay / start).read_bytes() if (overlay / start).exists() else b""
    bad = ainbedit.check_pushes(a, b, randomize.START_PUSHES) if b else ["file missing"]
    if bad:
        problems.append(f"new-game setup: {bad}")
    else:
        growth[start.as_posix()] = len(b) - len(a)
        print(f"new-game setup: {len(randomize.START_PUSHES)} pushes added (nut bag, Deku Nuts x0), nothing else changed")
elif (overlay / start).exists():
    problems.append("new-game setup in the overlay although no starting Deku Nuts")

opening = Path("Event/EventFlow/OpeningEvent.bfevfl.zs")
if json.loads((overlay / "seed.json").read_text(encoding="utf-8")).get("intro_skip"):
    from evfl import Flow
    a, b = unzs((ROMFS / opening).read_bytes()), unzs((overlay / opening).read_bytes())
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    fb = Flow(b)
    if len(a) != len(b) or fb.entry_points()["Opening_LoadingKokiriForest"][0] != 14 or fb.event(23)["next"] != 10 or len(diff) > 2:
        problems.append(f"OpeningEvent: unexpected edit ({len(diff)} bytes differ)")
    else:
        print(f"intro skip: OpeningEvent differs in {len(diff)} bytes (loading entry -> 14, event 23 -> 10)")
elif (overlay / opening).exists():
    problems.append("OpeningEvent in the overlay although the intro is kept")

rstb = next((ROMFS / "System" / "Resource").glob("ResourceSizeTable.Product.*.rsizetable.zs"))
a, b = unzs(rstb.read_bytes()), unzs((overlay / rstb.relative_to(ROMFS)).read_bytes())
ncrc, ncrc_b = struct.unpack_from("<I", a, 14)[0], struct.unpack_from("<I", b, 14)[0]
expect = {binascii.crc32(p.encode()): g for p, g in growth.items() if g}
ta = dict(struct.unpack_from("<II", a, 22 + 8 * i) for i in range(ncrc))
tb_list = [struct.unpack_from("<II", b, 22 + 8 * i) for i in range(ncrc_b)]
tb = dict(tb_list)
for h, va in ta.items():
    vb = tb.get(h)
    if vb is None or vb - va != expect.get(h, 0):
        problems.append(f"size table entry 0x{h:08x}: {va} -> {vb}, expected +{expect.get(h, 0)}")
want_new = {binascii.crc32(p.encode()): len(d) for p, d in new_sizes.items()}
for h in set(tb) - set(ta):
    if h not in want_new or tb[h] < want_new.pop(h):
        problems.append(f"size table: unexpected or too small new entry 0x{h:08x}")
problems += [f"size table: new pack 0x{h:08x} has no entry" for h in want_new]
if [h for h, _ in tb_list] != sorted(h for h, _ in tb_list):
    problems.append("size table: entries not sorted")
if a[22 + 8 * ncrc:] != b[22 + 8 * ncrc_b:]:
    problems.append("size table name entries changed")

print(f"{len(files)} map files, {len(seen)} chests changed, {len(spoiler)} chests in the spoiler log")
print("OK" if not problems else f"{len(problems)} PROBLEMS:\n  " + "\n  ".join(problems[:40]))
sys.exit(1 if problems else 0)
