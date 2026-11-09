# Hookshot Randomized

An item randomizer for [**Hookshot**](https://github.com/NotRexed/Hookshot), the native PC recompilation of the
Nintendo Switch 2 *Ocarina of Time* remake (built with HookshotBuilder into `hookshot.exe`).

It shuffles what the game's chests hold, opens up the world, skips cutscenes and fixes the problems an
out-of-order playthrough runs into. The game's own files are never changed. Each seed is a folder of replacement
files that `hookshot.exe` loads through `HOS_FS_OVERLAY`, and each seed plays from its own save folder.

> This repository contains **no game files**. You need your own copy of the game, recompiled with HookshotBuilder.
> Fan project, not affiliated with or endorsed by Nintendo.

## Requirements

- Windows
- The recompiled game from [Hookshot](https://github.com/NotRexed/Hookshot): the folder HookshotBuilder made, with
  `romfs\` and `bin\hookshot.exe`
- [Python 3.14](https://www.python.org/downloads/) or newer (only the standard library is used; 3.14 is needed for
  `compression.zstd`). Python from python.org or `uv python install 3.14` both work.

## Use

1. Download or clone this repository anywhere **outside** the game's folder (a recomp update replaces that whole
   folder).
2. Run `Randomizer.bat`. On the first start it asks for the game's folder, then reads the game's chests (about a
   minute).
3. Pick your options and click **Make seed & play**. On the title screen, pick New Game.

| Button | Does |
| --- | --- |
| Make seed & play | Makes a seed (empty seed box = random), checks it, starts the game on it |
| Make seed only | The same, without starting the game |
| Continue seed | Starts the current seed again, with its saves |
| Normal game | Starts the game without the randomizer, with your normal saves |
| Spoiler log | Every chest and what it holds |

While a randomized game runs, its window is titled "Hookshot Randomized - seed N".

## Options

| Section | Option | Default |
| --- | --- | --- |
| Randomize | Shuffle chest items: 174 chests, anywhere. Only small keys, boss keys, maps and compasses stay in their own dungeon (shuffled between its chests); the items a dungeon normally gives, such as the Bomb Bag, the Bow or the Hookshot, are shuffled like everything else. **No logic**: a seed can be unbeatable. | on |
| Randomize | Dungeon items anywhere: small keys, boss keys, maps and compasses can also be in chests outside dungeons and, with shop shuffle, on shop shelves (never in another dungeon). They still count for their own dungeon: Navi or the item's name says which, and a small key shows that dungeon's key count. Experimental | off |
| Randomize | Shuffle shop items: 64 slots in 8 shops join the pool. Chest items are sold in shops and leave the shelf once owned; shop items can be in chests and other shops. Bottle contents and hearts only move between shops; the Happy Mask Shop is left alone. | off |
| Randomize | Event items (NPC gifts) | not built yet |
| Important items | Kokiri Sword stays in its chest | off |
| World | Open Kokiri Forest: the Kokiri at the exit lets you pass | on |
| World | Open Door of Time (and the Master Sword can be pulled) | on |
| Skips | Skip the intro: opening movie and Navi waking Link | on |
| Skips | Skip cutscenes: Saria's scenes, area intros, Kaepora Gaebora's talks | on |
| Items | Bombs need the Goron's Bracelet: the game's own rule; off lets a child hold bombs without it | off |
| Items | Bombs give the Bomb Bag: on, as in the game, the first bombs found in a chest bring the bag; off, bombs don't give the bag, and every chest holding bombs gives Bombchus instead until the Bomb Bag is found, then bombs (a big chest holding bombs always gives Bombchus) | off |
| Items | Bombchus without the Bomb Bag: a save starts with room for 10, so Bombchus work before the bag (the game has one count for bombs and Bombchus, so chest bombs before the bag work too) | off |
| Items | Magic items give the magic meter: the game's failsafe; off means only Great Fairies give magic | off |
| Bug fixes | Start with Deku Nuts (0 nuts): stops an adult item a child finds first (e.g. Ice Arrows) from taking the item bar and blocking every item | on |
| Game | Cheats page in the Insert menu: Hookshot's debug tools (Teleport, no-clip flight, age switch, inventory); starts the game with `HOS_OOT_CHEATS=1`, needs Hookshot v2.0.6+ | off |

The same options exist on the command line:

```
python -I tools\randomize.py --seed 123 [--closed-forest] [--closed-door] [--keep-intro] [--keep-cutscenes]
       [--bombs-need-bracelet] [--free-magic] [--no-deku-nut] [--keep Item_Sword_Kokiri] [--place PLACE=ITEM]
       [--shuffle-shops] [--dungeon-items-anywhere] [--bombs-give-bag] [--bombchus-without-bag]
python -I tools\verify_seed.py overlay
```

`--place` (or `"place"` in `launcher_settings.json`) forces items into chosen places for testing: a chest's hash, or
a shop slot as written in the spoiler log (`KokiriForest/Npc_Kokiri_001_Shop/6`). The launcher shows active test
placements with a Clear button.

## After a recomp update

Delete `tools\chests.json` and `tools\shops.json` (they're rebuilt on the next start), then make a new seed. Every patch checks the original
data before changing it, so a game update that moves something stops the build with an error rather than making a
broken seed.

## How it works

Everything is in `tools\`, Python standard library only:

| File | What |
| --- | --- |
| `gui.py`, `launcher.py` | The launcher window, and the older text menu; starting the game with the overlay and a per-seed save folder |
| `randomize.py` | Builds a seed: shuffle, world flags, all patches, spoiler log, `seed.json` |
| `verify_seed.py` | Checks a seed against the game's files: only the intended bytes changed, items as a whole kept |
| `nxfmt.py`, `bymledit.py` | zstd (with the game's dictionaries), SARC, BYML reading and in-place editing; resource size table |
| `bymlw.py` | BYML reading with types kept and writing, SARC writing: for new actor packs and added table rows |
| `shops.py` | Shop shuffle: the shop slots, new shop actors for chest items, item table rows, sold-out conditions |
| `evfl.py`, `instant.py`, `timelines.py`, `cutscenes.py` | BFEVFL v4 event flows; cutscene skips by rerouting flows or cutting timelines |
| `evfledit.py` | Adding events and actor actions to an event flow |
| `msbt.py` | MSBT message files: reading, writing, adding messages (with game-data tags such as a key count) |
| `keycredit.py`, `dungeoncredit.py` | Dungeon items outside their dungeon: chest events and map logic that credit their own dungeon, Navi's messages |
| `shopdungeon.py` | Dungeon items on shop shelves: a shelf actor, name and saved sold flag each, credited when bought |
| `ainb.py`, `ainbedit.py` | AINB 0x408 logic: reading, and adding item pushes to the new-game setup |
| `itempatches.py` | Item table (RSDB PouchActorInfo) and actor pack edits (no free magic) |
| `gamedata.py` | GameDataList and murmur3 names; new-save defaults |
| `window_title.py` | Renames the game window while it runs |
| `research\` | Tools used to work things out: searching game files, reading saves, dumping flows |

Notes on what was learned along the way are in the docstrings: the file formats, why each patch looks the way it
does, and what was tested in game.

## Bugs, ideas and contributing

- **Found a bug?** [Open an issue](https://github.com/KAMOX06/hookshot-randomized/issues/new/choose) with the bug
  report form: the seed number, the options and the steps to make it happen.
- **Have an idea?** Use the feature request form on the same page.
- **Questions or a fun seed to share?** [Discussions](https://github.com/KAMOX06/hookshot-randomized/discussions).
- **Want to help with code?** See [CONTRIBUTING.md](CONTRIBUTING.md).

## Credits

- [**Hookshot**](https://github.com/NotRexed/Hookshot) by NotRexed: the native recompilation this randomizer is built
  on. Its file overlay (`HOS_FS_OVERLAY`) and per-save user folder (`--user`) are what make seeds possible without
  touching the game's files. Hookshot is a separate project under its own license (GPL-3.0); none of its code is
  included here.

## License

[MIT](LICENSE). Covers this repository's code only; the game and Hookshot are not part of it.

## Roadmap

- Event item shuffle (NPC gifts, e.g. Saria's Ocarina)
- Shop shuffle: quantity signs under the shelves that match the new item
- Open child dungeons: enter the Deku Tree, Dodongo's Cavern and Jabu-Jabu's Belly without their story
  requirements (World option)
- Logic, so every seed is beatable (with the vanilla rules as options: bombs need the bracelet, magic items need a
  Great Fairy)
