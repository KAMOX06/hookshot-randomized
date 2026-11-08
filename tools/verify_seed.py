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
spoiler = {}
for line in (overlay / "spoiler.txt").read_text(encoding="utf-8").splitlines()[3:]:
    p = line.split()
    spoiler[int(p[2])] = (p[0], p[3])

dropped_events = json.loads((overlay / "seed.json").read_text(encoding="utf-8")).get("dropped_events", {})
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
        if o == n:
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

chests = json.loads((Path(__file__).parent / "chests.json").read_text(encoding="utf-8"))
first = {}
for c in chests:
    first.setdefault(c["hash"], c)
orig = collections.Counter(first[h]["drop"] for h in spoiler)
now = collections.Counter(item for _, item in spoiler.values())
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
    info = Path(itempatches.POUCH_INFO)
    a, b = unzs((ROMFS / info).read_bytes()), unzs((overlay / info).read_bytes()) if (overlay / info).exists() else b""
    ra = {r["__RowId"]: r for r in Byml(a).root}
    rb = {r["__RowId"]: r for r in Byml(b).root} if b else {}
    gone = [n for n, r in ra.items() if r.get("ExpansionAvility") == "MPMaxUP" and "FairyBless" not in n]
    bad = len(a) != len(b) or ra.keys() != rb.keys()
    for n, r in ra.items():
        if bad:
            break
        want_row = {k: v for k, v in r.items() if k not in itempatches.RSDB_MAGIC_KEYS} if n in gone else r
        bad = rb[n] != want_row
    if bad or not gone:
        problems.append("magic: PouchActorInfo: unexpected edit or not patched")
    print(f"magic: {len(gone)} item table rows and {len(want)} item packs without magic (Great Fairies keep theirs)")
elif seedinfo.get("magic_packs"):
    problems.append("magic packs in the overlay although magic items keep giving magic")
elif (overlay / "RSDB").exists():
    problems.append("item table in the overlay although magic items keep giving magic")

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
ncrc = struct.unpack_from("<I", a, 14)[0]
expect = {binascii.crc32(p.encode()): g for p, g in growth.items() if g}
for i in range(ncrc):
    h, va = struct.unpack_from("<II", a, 22 + 8 * i)
    hb, vb = struct.unpack_from("<II", b, 22 + 8 * i)
    if h != hb or vb - va != expect.get(h, 0):
        problems.append(f"size table entry 0x{h:08x}: {va} -> {vb}, expected +{expect.get(h, 0)}")
if a[22 + 8 * ncrc:] != b[22 + 8 * ncrc:]:
    problems.append("size table name entries changed")

print(f"{len(files)} map files, {len(seen)} chests changed, {len(spoiler)} chests in the spoiler log")
print("OK" if not problems else f"{len(problems)} PROBLEMS:\n  " + "\n  ".join(problems[:40]))
sys.exit(1 if problems else 0)
