# Contributing

Thanks for helping! Bug reports, ideas and code are all welcome.

## Reporting bugs and ideas

- **Bugs:** open an issue with the **Bug report** form. The seed number, the options (the first line of
  `overlay\spoiler.txt`) and the steps to make it happen matter most.
- **Ideas:** use the **Feature request** form.
- **Questions:** ask in [Discussions](https://github.com/KAMOX06/hookshot-randomized/discussions).
- Check first that it doesn't also happen in the normal game (the launcher's **Normal game** button). If it does,
  it belongs to [Hookshot](https://github.com/NotRexed/Hookshot).
- There's no logic yet: an item that's out of reach in a seed isn't a bug (unless the seed was made with a future
  logic option).

## The one hard rule

**Never commit game files**, or anything copied or extracted from them: no romfs files, no `overlay\` folders, no
`chests.json`, no saves. The `.gitignore` keeps the usual ones out. The repository holds only code that edits the
player's own files on their machine.

## Setting up

- Windows, [Python 3.14+](https://www.python.org/downloads/) (standard library only, no packages to install).
- Your own recompiled game. Point the tools at it with `game_folder.txt` in the repo root (one line: the game's
  folder), or the `OOT_GAME` environment variable. The launcher writes `game_folder.txt` for you on first start.

Build and check a seed from the command line:

```
python -I tools\randomize.py --seed 123 --out overlay
python -I tools\verify_seed.py overlay
```

`randomize.py --help` lists every option. `--place HASH=ITEM` forces an item into a chest, which is handy for
testing (chest hashes are in the spoiler log).

To try a seed in game, use the launcher's **Continue seed** button, or run `hookshot.exe` with the environment
variable `HOS_FS_OVERLAY` set to the overlay folder and `--user <folder>` for a separate save folder.

## How patches are written

Look at how the existing ones work (`tools\randomize.py`, `cutscenes.py`, `itempatches.py`, `ainbedit.py`) and
follow the same habits:

- **Check the original before changing it.** Every patch asserts what it expects to find (an event's action, a
  node's name, a value) and stops with a clear error if the game data is different. A recomp update must stop a
  build, never quietly produce a broken seed.
- **Change as little as possible**, in place where you can, and keep file sizes unless there's no other way. If a
  file grows, raise its entry in the resource size table (see `raise_sizes` in `bymledit.py`).
- **Add a check to `verify_seed.py`** that confirms only the intended bytes changed.
- **Say why in the docstring:** what the patch does, what you found in the game data, and what was tested in game.
  Those notes are how the next person understands the patch.
- **Test in game** before calling something fixed, and say in the pull request what you tested and on which seed.
- Give starting items through the game's new-game setup (`ainbedit.add_pushes`), not by changing GameData
  defaults: a flag alone leaves the inventory half made.
- New user-facing options go into `tools\gui.py` (`OPTIONS`), `randomize.py` (a `build()` argument and a
  command-line flag), `seed.json` and the README's options table.

`tools\research\` has the scripts used to work things out: searching game files for a string, reading saves,
dumping event flows and logic. They're a good place to start when investigating a bug.

## Pull requests

- One change per pull request, with a short description of what and why.
- Say how you tested it (seed number, steps, in game or not).
- Make sure `verify_seed.py` passes on a seed built with your change.
