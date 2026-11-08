"""Randomizer launcher: a menu before the game.

  1  New randomizer seed  - makes a seed (forest exit and Door of Time open, intro skipped) and starts it
  2  Continue this seed   - starts the current seed again
  3  Normal game          - starts the game without the randomizer

Each seed plays from its own save folder, saves/<seed>, holding only copies of the game's account.sav and
option.sav (so settings carry over and no save slot exists yet: the title screen goes straight to a new game).
The game's own user folder, and the saves there, are never written.
"""
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from nxfmt import GAME

HERE = Path(__file__).parent
ROOT = HERE.parent
OVERLAY = ROOT / "overlay"
SAVES = ROOT / "saves"
# the recomp has kept its exe at the top of the game folder and, since v2.0.x, in bin\; the game still runs from the
# game folder (romfs, user, logs)
EXE = next((p for p in (GAME / "bin" / "hookshot.exe", GAME / "hookshot.exe") if p.exists()), GAME / "bin" / "hookshot.exe")


def current_seed():
    try:
        return json.loads((OVERLAY / "seed.json").read_text(encoding="utf-8"))["seed"]
    except (OSError, ValueError, KeyError):
        return None


def prepare_saves(seed):
    """saves/<seed>, created with the game's settings but no save slot."""
    dst = SAVES / str(seed)
    if dst.exists():
        return dst
    src = GAME / "user" / "save"
    for account in (src.iterdir() if src.exists() else []):
        if account.is_dir():
            (dst / "save" / account.name).mkdir(parents=True, exist_ok=True)
            for name in ("account.sav", "option.sav"):
                if (account / name).exists():
                    shutil.copy2(account / name, dst / "save" / account.name / name)
    dst.mkdir(parents=True, exist_ok=True)
    return dst


def start(overlay, user_dir=None):
    env = dict(os.environ)
    env.pop("HOS_FS_OVERLAY", None)
    if overlay:
        env["HOS_FS_OVERLAY"] = str(OVERLAY)
    args = [str(EXE)] + (["--user", str(user_dir)] if user_dir else [])
    subprocess.Popen(args, cwd=str(GAME), env=env, creationflags=subprocess.DETACHED_PROCESS)


def new_seed():
    from randomize import build, OPEN_FOREST, OPEN_DOOR
    seed = random.SystemRandom().randrange(10**9)
    print(f"\nMaking seed {seed} ...")
    print(build(seed, OVERLAY, {**OPEN_FOREST, **OPEN_DOOR}, True))
    check = subprocess.run([sys.executable, "-I", str(HERE / "verify_seed.py"), str(OVERLAY)], capture_output=True, text=True)
    if check.returncode:
        print(check.stdout, check.stderr)
        print("The seed failed its checks, so the game was not started.")
        return False
    print("Checks passed. Spoiler log:", OVERLAY / "spoiler.txt")
    start(True, prepare_saves(seed))
    return True


def main():
    if not EXE.exists():
        print(f"The game was not found at {EXE}. Set OOT_GAME to its folder.")
        input("Press Enter to close.")
        return
    seed = current_seed()
    print("Ocarina of Time recomp - chest randomizer\n")
    print("  1  New randomizer seed")
    print(f"  2  Continue seed {seed}" if seed is not None else "  2  (no seed yet)")
    print("  3  Normal game")
    choice = input("\nChoose 1, 2 or 3: ").strip()
    if choice == "1":
        if not new_seed():
            input("Press Enter to close.")
            return
    elif choice == "2" and seed is not None:
        start(True, prepare_saves(seed))
    elif choice == "3":
        start(False)
    else:
        print("Nothing started.")
        input("Press Enter to close.")
        return
    print("\nStarting the game. On a new seed, pick New Game on the title screen.")


if __name__ == "__main__":
    main()
