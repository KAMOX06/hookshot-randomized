"""Minimal readers for the game's container formats: zstd (with ZsDic dictionaries), SARC and BYML (read-only)."""
import os
import struct
import compression.zstd as zstd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GAME_FOLDER_FILE = ROOT / "game_folder.txt"   # one line: the game's folder; written by the launcher, not committed


def game_folder():
    """The recompiled game's folder (HookshotBuilder's output, holding romfs\\ and bin\\hookshot.exe): the OOT_GAME
    environment variable, else game_folder.txt, else None."""
    if os.environ.get("OOT_GAME"):
        return Path(os.environ["OOT_GAME"])
    try:
        text = GAME_FOLDER_FILE.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return None
    return Path(text) if text else None


GAME = game_folder() or Path("game-folder-not-set")
ROMFS = GAME / "romfs"
# Hookshot v2.1.0+ serves its own copy of the resource size table in place of the game's (prompts\rsizetable.zs:
# the game's with the entry of Font2/Scalable/External...bfttf raised for its controller icon font). An overlay's
# table replaces that copy too, so a seed's table must start from it: started from the game's, the font no longer
# fits and the game crashes on start (exception 0xc0000005 in sead::MainThread, 2026-11-12).
HOOKSHOT_RSTB = GAME / "prompts" / "rsizetable.zs"


def rstb_path():
    """(romfs-relative path of the resource size table, the file its data comes from: Hookshot's copy if any)."""
    rel = next((ROMFS / "System" / "Resource").glob("ResourceSizeTable.Product.*.rsizetable.zs"))
    return rel.relative_to(ROMFS).as_posix(), (HOOKSHOT_RSTB if HOOKSHOT_RSTB.exists() else rel)


def parse_sarc(data):
    assert data[:4] == b"SARC", data[:4]
    data_off = struct.unpack_from("<I", data, 0x0C)[0]
    assert data[0x14:0x18] == b"SFAT"
    count = struct.unpack_from("<H", data, 0x1A)[0]
    nodes = []
    for i in range(count):
        h, attr, start, end = struct.unpack_from("<IIII", data, 0x20 + i * 16)
        nodes.append((attr, start, end))
    sfnt = 0x20 + count * 16
    assert data[sfnt:sfnt + 4] == b"SFNT"
    names_base = sfnt + 8
    out = {}
    for attr, start, end in nodes:
        noff = names_base + (attr & 0xFFFFFF) * 4
        name = data[noff:data.index(b"\0", noff)].decode()
        out[name] = data[data_off + start:data_off + end]
    return out


_dicts = None


def dicts():
    global _dicts
    if _dicts is None:
        raw = zstd.decompress((ROMFS / "Pack" / "ZsDic.pack.zs").read_bytes())
        _dicts = {name: zstd.ZstdDict(blob) for name, blob in parse_sarc(raw).items()}
    return _dicts


def unzs(data):
    if not data.startswith(b"\x28\xb5\x2f\xfd"):
        return data
    try:
        return zstd.decompress(data)
    except zstd.ZstdError:
        pass
    for d in dicts().values():
        try:
            return zstd.decompress(data, zstd_dict=d)
        except zstd.ZstdError:
            continue
    raise ValueError("no dictionary fits")


class Byml:
    def __init__(self, data):
        self.d = data
        if data[:2] == b"YB":
            self.e = "<"
        elif data[:2] == b"BY":
            self.e = ">"
        else:
            raise ValueError("not BYML")
        self.version, key_off, str_off, root_off = struct.unpack_from(self.e + "HIII", data, 2)
        self.keys = self._strtab(key_off)
        self.strs = self._strtab(str_off)
        self.root = self._node(data[root_off], root_off) if root_off else None

    def u32(self, o):
        return struct.unpack_from(self.e + "I", self.d, o)[0]

    def u24(self, o):
        b = self.d[o:o + 3]
        return int.from_bytes(b, "little" if self.e == "<" else "big")

    def _strtab(self, off):
        if not off:
            return []
        n = self.u24(off + 1)
        offs = [self.u32(off + 4 + 4 * i) for i in range(n + 1)]
        return [self.d[off + offs[i]:self.d.index(b"\0", off + offs[i])].decode("utf-8", "replace") for i in range(n)]

    def _value(self, t, v):
        e = self.e
        if t == 0xA0: return self.strs[v]
        if t == 0xD0:
            if v not in (0, 1):   # the game wants exactly 0 or 1 (a byte-swapped 1 read as true here, not in game)
                raise ValueError(f"bool holding {v:#x}")
            return bool(v)
        if t == 0xD1: return struct.unpack(e + "i", struct.pack(e + "I", v))[0]
        if t == 0xD2: return struct.unpack(e + "f", struct.pack(e + "I", v))[0]
        if t == 0xD3: return v
        if t == 0xD4: return struct.unpack_from(e + "q", self.d, v)[0]
        if t == 0xD5: return struct.unpack_from(e + "Q", self.d, v)[0]
        if t == 0xD6: return struct.unpack_from(e + "d", self.d, v)[0]
        if t == 0xFF: return None
        if t == 0xA1:
            n = self.u32(v)
            return self.d[v + 4:v + 4 + n]
        if t in (0xC0, 0xC1, 0x20, 0x21):
            return self._node(t, v)
        return ("?%02x" % t, v)

    def _node(self, t, off):
        n = self.u24(off + 1)
        if t == 0xC0:
            types = self.d[off + 4:off + 4 + n]
            base = off + 4 + ((n + 3) & ~3)
            return [self._value(types[i], self.u32(base + 4 * i)) for i in range(n)]
        if t == 0xC1:
            out = {}
            for i in range(n):
                e = off + 4 + 8 * i
                out[self.keys[self.u24(e)]] = self._value(self.d[e + 3], self.u32(e + 4))
            return out
        if t in (0x20, 0x21):
            hw = 4 if t == 0x20 else 8
            ent = hw + 4
            types = self.d[off + 4 + n * ent:off + 4 + n * ent + n]
            out = {}
            for i in range(n):
                e = off + 4 + ent * i
                h = int.from_bytes(self.d[e:e + hw], "little" if self.e == "<" else "big")
                out["#%x" % h] = self._value(types[i], self.u32(e + hw))
            return out
        raise ValueError("node type %02x" % t)


def load_byml(path):
    return Byml(unzs(Path(path).read_bytes())).root
