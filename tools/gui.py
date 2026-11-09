"""Hookshot Randomized: the launcher window (Tk). Pick options, make a seed, play it.

- Make seed & play: builds a seed with the options shown (blank seed = a random one), checks it with verify_seed.py,
  then starts the game on it from saves/<seed> (see launcher.py, whose save and start code this uses).
- Continue: starts the current seed (overlay/) again. Normal game: the game without the randomizer.
- The options last used are kept in launcher_settings.json next to Randomizer.bat.
- While a randomized game runs, its window is titled "Hookshot Randomized - seed N" (window_title.py).
"""
import json
import os
import queue
import random
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

sys.path.insert(0, str(Path(__file__).parent))
import launcher
from launcher import EXE, HERE, OVERLAY, ROOT, current_seed, prepare_saves

TITLE = "Hookshot Randomized"
SETTINGS = ROOT / "launcher_settings.json"
NO_WINDOW = 0x08000000   # CREATE_NO_WINDOW

# (key, label, default, help, available)
OPTIONS = [
    ("Randomize", [
        ("shuffle_chests", "Shuffle chest items", True, "Every chest's item is shuffled (dungeon items stay in their dungeon). No logic.", True),
        ("shuffle_shops", "Shuffle shop items", False,
         "Shop items join the pool: they can be in chests and other shops, and chest items in shops (random price, "
         "10 to 90). Bottle contents and hearts stay in shops; the Happy Mask Shop is left alone.", True),
        ("shuffle_events", "Shuffle event items (NPC gifts)", False, "Not built yet (e.g. Saria's ocarina).", False),
    ]),
    ("Important items", [
        ("keep_kokiri_sword", "Kokiri Sword stays in its chest", False,
         "The Kokiri Sword isn't shuffled: it's in its usual chest in Kokiri Forest, with its usual scene.", True),
    ]),
    ("World", [
        ("open_forest", "Open Kokiri Forest", True, "The Kokiri guarding the forest exit lets you pass from the start.", True),
        ("open_door", "Open Door of Time", True, "The Door of Time starts open and the Master Sword can be pulled.", True),
    ]),
    ("Skips", [
        ("intro_skip", "Skip the intro", True, "No opening movie or Navi waking Link: the game starts in Link's house.", True),
        ("cutscene_skip", "Skip cutscenes", True, "Saria's scenes, area intros and Kaepora Gaebora's talks.", True),
    ]),
    ("Items", [
        ("bombs_need_bracelet", "Bombs need the Goron's Bracelet", False,
         "The game's own rule: without the bracelet a bomb vanishes as soon as it's taken out.", True),
        ("bombs_give_bag", "Bombs give the Bomb Bag", False,
         "The first bombs found (in a chest or a shop) bring the Bomb Bag, as Deku Nuts and Sticks bring theirs. "
         "Off: as in the game, bombs found before the bag are lost.", True),
        ("bombchus_without_bag", "Bombchus without the Bomb Bag", False,
         "Bombchus found or bought before the Bomb Bag can be used (up to 10). The game keeps one count for bombs and "
         "Bombchus, "
         "so bombs found in a chest before the bag work too; shops still sell bombs only with the bag.", True),
        ("free_magic", "Magic items give the magic meter", False,
         "The game's failsafe: the Lens of Truth, spells and magic arrows also give magic. Off: only Great Fairies do.", True),
    ]),
    ("Bug fixes", [
        ("start_deku_nut", "Start with Deku Nuts (0 nuts)", True,
         "Keeps adult items a child finds first (e.g. Ice Arrows) out of the item bar, where they block every item.", True),
    ]),
    ("Game", [
        ("debug_menu", "Cheats page in the Insert menu", False,
         "Hookshot's debug tools: Teleport, no-clip flight, age switch, inventory. Takes effect when the game starts; "
         "needs Hookshot v2.0.6 or newer.", True),
    ]),
]   # ("Game" options are how the game starts, not part of a seed)
DEFAULTS = {k: d for _, opts in OPTIONS for k, _, d, _, _ in opts}
KEEP_ITEMS = {"keep_kokiri_sword": "Item_Sword_Kokiri"}   # Important items toggles -> the item kept in its chest


def _saved():
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8-sig"))   # -sig: hand-edited files may have a BOM
    except (OSError, ValueError):
        return {}


def load_settings():
    saved = _saved()
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def test_placements():
    """{chest hash: item} from launcher_settings.json's "place" (test seeds), or {}."""
    place = _saved().get("place") or {}
    return place if isinstance(place, dict) else {}


def save_settings(values, place=None):
    """Save the options, keeping the test placements unless new ones are given."""
    data = {**values, "place": test_placements() if place is None else place}
    if not data["place"]:
        data.pop("place")
    try:
        SETTINGS.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def describe(info):
    """Short text of a seed.json's settings."""
    if not info:
        return "none yet"
    parts = []
    if info.get("shuffle_chests", True):
        parts.append("chests")
    if info.get("shuffle_shops"):
        parts.append("shops")
    flags = info.get("flags", {})
    if flags.get("KokiriForest_Npc_Kokiri_007_Child_Blocker_00") is False:
        parts.append("open forest")
    if flags.get("Market_Child_TimeTempleDoor_Opened"):
        parts.append("open door")
    if info.get("intro_skip"):
        parts.append("intro skip")
    if info.get("cutscene_skip"):
        parts.append("cutscene skips")
    if info.get("bombs_need_bracelet") is False:
        parts.append("bombs without bracelet")
    if info.get("bombs_give_bag"):
        parts.append("bombs give the bag")
    if info.get("bombchus_without_bag"):
        parts.append("Bombchus without bag")
    if info.get("free_magic") is False:
        parts.append("no free magic")
    for item in info.get("keep_items", []):
        parts.append(f"{item.replace('Item_', '').replace('_', ' ')} kept")
    if info.get("start_deku_nut"):
        parts.append("starting Deku Nuts")
    if info.get("place"):
        parts.append(f"{len(info['place'])} test placements")
    return f"{info['seed']}  ({', '.join(parts) or 'nothing changed'})"


def seed_info():
    try:
        return json.loads((OVERLAY / "seed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class QueueWriter:
    def __init__(self, q):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(s)

    def flush(self):
        pass


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.busy = False
        root.title(TITLE)
        root.minsize(560, 600)
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Help.TLabel", foreground="#666666")
        style.configure("Play.TButton", font=("Segoe UI", 10, "bold"))

        main = ttk.Frame(root, padding=14)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text=TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(main, text="Ocarina of Time recomp randomizer", style="Help.TLabel").pack(anchor="w", pady=(0, 10))

        seed_row = ttk.Frame(main)
        seed_row.pack(fill="x", pady=(0, 8))
        ttk.Label(seed_row, text="Seed:").pack(side="left")
        self.seed = tk.StringVar()
        ttk.Entry(seed_row, textvariable=self.seed, width=16).pack(side="left", padx=6)
        ttk.Button(seed_row, text="Random", command=self.random_seed).pack(side="left")
        ttk.Label(seed_row, text="leave empty for a random seed", style="Help.TLabel").pack(side="left", padx=8)

        settings = load_settings()
        self.vars = {}
        grid = ttk.Frame(main)
        grid.pack(fill="x")
        for col in (0, 1):
            grid.columnconfigure(col, weight=1, uniform="opts")
        for i, (group, opts) in enumerate(OPTIONS):
            box = ttk.LabelFrame(grid, text=group, padding=(10, 6))
            box.grid(row=i // 2, column=i % 2, sticky="nsew", padx=(0 if i % 2 == 0 else 6, 0), pady=4)
            for key, label, default, help_text, available in opts:
                v = tk.BooleanVar(value=settings[key] if available else False)
                self.vars[key] = v
                cb = ttk.Checkbutton(box, text=label + ("" if available else "  (coming later)"), variable=v,
                                     command=self.remember)
                cb.pack(anchor="w")
                if not available:
                    cb.state(["disabled"])
                ttk.Label(box, text=help_text, style="Help.TLabel", wraplength=230, justify="left").pack(
                    anchor="w", padx=(22, 0), pady=(0, 4))

        self.place_row = ttk.Frame(main)
        self.place_text = tk.StringVar()
        ttk.Label(self.place_row, text="Test placements:", foreground="#a05000").pack(side="left")
        ttk.Label(self.place_row, textvariable=self.place_text, foreground="#a05000", wraplength=380).pack(
            side="left", padx=6)
        ttk.Button(self.place_row, text="Clear", command=self.clear_place).pack(side="right")
        self.show_place()

        self.current = tk.StringVar()
        cur = ttk.Frame(main)
        cur.pack(fill="x", pady=(10, 4))
        ttk.Label(cur, text="Current seed:").pack(side="left")
        ttk.Label(cur, textvariable=self.current).pack(side="left", padx=6)

        buttons = ttk.Frame(main)
        buttons.pack(fill="x", pady=4)
        self.play_btn = ttk.Button(buttons, text="Make seed & play", style="Play.TButton", command=lambda: self.make(True))
        self.play_btn.pack(side="left")
        self.make_btn = ttk.Button(buttons, text="Make seed only", command=lambda: self.make(False))
        self.make_btn.pack(side="left", padx=6)
        self.cont_btn = ttk.Button(buttons, text="Continue seed", command=self.continue_seed)
        self.cont_btn.pack(side="left")
        ttk.Button(buttons, text="Normal game", command=self.normal_game).pack(side="right")
        ttk.Button(buttons, text="Spoiler log", command=self.open_spoiler).pack(side="right", padx=6)

        self.log = tk.Text(main, height=10, wrap="word", font=("Consolas", 9), state="disabled",
                           background="#f6f6f6", relief="flat")
        self.log.pack(fill="both", expand=True, pady=(8, 0))

        self.refresh()
        if not EXE.exists():
            self.write(f"The game was not found at {EXE}. Set OOT_GAME to its folder.\n")
        root.after(100, self.pump)

    # helpers
    def write(self, s):
        self.log.configure(state="normal")
        self.log.insert("end", s)
        self.log.see("end")
        self.log.configure(state="disabled")

    def pump(self):
        try:
            while True:
                item = self.q.get_nowait()
                if callable(item):
                    item()
                else:
                    self.write(item)
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def refresh(self):
        info = seed_info()
        self.current.set(describe(info))
        state = ["!disabled"] if info and not self.busy else ["disabled"]
        self.cont_btn.state(state)
        for b in (self.play_btn, self.make_btn):
            b.state(["disabled"] if self.busy else ["!disabled"])

    def values(self):
        return {k: v.get() for k, v in self.vars.items()}

    def remember(self):
        save_settings(self.values())

    def show_place(self):
        place = test_placements()
        if place:
            self.place_text.set(", ".join(f"{item} in {'chest ' if str(h).isdigit() else ''}{h}"
                                          for h, item in place.items()))
            self.place_row.pack(fill="x", pady=(8, 0))
        else:
            self.place_row.pack_forget()

    def clear_place(self):
        save_settings(self.values(), place={})
        self.show_place()

    def random_seed(self):
        self.seed.set(str(random.SystemRandom().randrange(10**9)))

    # actions
    def make(self, play):
        if self.busy:
            return
        text = self.seed.get().strip()
        if text and not text.isdigit():
            messagebox.showerror(TITLE, "The seed must be a whole number (or empty for a random one).")
            return
        seed = int(text) if text else random.SystemRandom().randrange(10**9)
        values = self.values()
        save_settings(values)
        self.busy = True
        self.refresh()
        threading.Thread(target=self._make, args=(seed, values, play), daemon=True).start()

    def _make(self, seed, o, play):
        from randomize import build, OPEN_FOREST, OPEN_DOOR
        out = QueueWriter(self.q)
        old = sys.stdout
        sys.stdout = out
        ok = False
        try:
            print(f"Making seed {seed} ...\n")
            flags = {**(OPEN_FOREST if o["open_forest"] else {}), **(OPEN_DOOR if o["open_door"] else {})}
            print(build(seed, OVERLAY, flags, intro_skip=o["intro_skip"], cutscene_skip=o["cutscene_skip"],
                        bombs_need_bracelet=o["bombs_need_bracelet"], free_magic=o["free_magic"],
                        shuffle_chests=o["shuffle_chests"], place=test_placements(),
                        start_deku_nut=o["start_deku_nut"], shuffle_shops=o["shuffle_shops"],
                        bombchus_without_bag=o["bombchus_without_bag"], bombs_give_bag=o["bombs_give_bag"],
                        keep_items=[item for key, item in KEEP_ITEMS.items() if o[key]]) + "\n")
            check = subprocess.run([sys.executable, "-I", str(HERE / "verify_seed.py"), str(OVERLAY)],
                                   capture_output=True, text=True, creationflags=NO_WINDOW)
            if check.returncode:
                print(check.stdout + check.stderr)
                print("The seed failed its checks, so the game was not started.\n")
            else:
                print(f"Checks passed. Spoiler log: {OVERLAY / 'spoiler.txt'}\n")
                ok = True
        except Exception as e:   # shown in the window instead of a silent thread death
            print(f"\nError: {type(e).__name__}: {e}\n")
        finally:
            sys.stdout = old

        def done():
            self.busy = False
            self.refresh()
            if ok and play:
                self.launch(seed)
        self.q.put(done)

    def launch(self, seed):
        if not EXE.exists():
            messagebox.showerror(TITLE, f"The game was not found at {EXE}.")
            return
        proc = start_game(True, prepare_saves(seed), cheats=self.vars["debug_menu"].get())
        keep_title(proc.pid, f"{TITLE} - seed {seed}")
        self.write(f"Game started on seed {seed}. On a new seed, pick New Game on the title screen.\n")

    def continue_seed(self):
        info = seed_info()
        if info:
            self.launch(info["seed"])

    def normal_game(self):
        if not EXE.exists():
            messagebox.showerror(TITLE, f"The game was not found at {EXE}.")
            return
        start_game(False, cheats=self.vars["debug_menu"].get())
        self.write("Normal game started (no randomizer, your normal saves).\n")

    def open_spoiler(self):
        p = OVERLAY / "spoiler.txt"
        if p.exists():
            os.startfile(p)
        else:
            messagebox.showinfo(TITLE, "No seed made yet.")


def start_game(overlay, user_dir=None, cheats=False):
    env = dict(os.environ)
    env.pop("HOS_FS_OVERLAY", None)
    if overlay:
        env["HOS_FS_OVERLAY"] = str(OVERLAY)
    env.pop("HOS_OOT_CHEATS", None)
    if cheats:   # (Hookshot's Insert menu shows its Cheats row only with this set: gpu/vk/vk_menu.cpp menu_init)
        env["HOS_OOT_CHEATS"] = "1"
    args = [str(EXE)] + (["--user", str(user_dir)] if user_dir else [])
    return subprocess.Popen(args, cwd=str(launcher.GAME), env=env, creationflags=subprocess.DETACHED_PROCESS)


def keep_title(pid, title):
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = pyw if pyw.exists() else Path(sys.executable)
    subprocess.Popen([str(exe), "-I", str(HERE / "window_title.py"), str(pid), str(EXE), title],
                     creationflags=subprocess.DETACHED_PROCESS | NO_WINDOW)


def find_exe(folder):
    folder = Path(folder)
    return next((p for p in (folder / "bin" / "hookshot.exe", folder / "hookshot.exe") if p.exists()), None)


def ask_game_folder():
    """Ask for the game's folder until one holds romfs\\ and hookshot.exe; save it to game_folder.txt.
    Returns False if the user gives up."""
    from tkinter import filedialog
    from nxfmt import GAME_FOLDER_FILE
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo(TITLE, "Pick the recompiled game's folder: the one HookshotBuilder made, holding romfs and "
                               "hookshot.exe (in bin).")
    while True:
        folder = filedialog.askdirectory(title="The recompiled game's folder")
        if not folder:
            root.destroy()
            return False
        if (Path(folder) / "romfs").is_dir() and find_exe(folder):
            GAME_FOLDER_FILE.write_text(str(Path(folder)) + "\n", encoding="utf-8")
            root.destroy()
            return True
        messagebox.showerror(TITLE, f"No romfs folder and hookshot.exe in:\n{folder}")


def main():
    if not EXE.exists():
        if ask_game_folder():   # the game folder is read at import: start again with it
            subprocess.Popen([sys.executable, "-I", str(Path(__file__).resolve())])
        return
    if not (HERE / "chests.json").exists():
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(TITLE, "First start: reading the game's chests (takes a minute).")
        root.destroy()
        subprocess.run([sys.executable, "-I", str(HERE / "chests.py")], creationflags=NO_WINDOW)
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
