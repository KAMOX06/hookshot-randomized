"""BYML (v7) reading with types kept, and writing: for files the randomizer makes or rebuilds (new actor packs, rows
added to RSDB tables), where bymledit's in-place edits are not enough.

loads(data) gives plain Python values where the type is the default one (dict, list, str, bool, None, int = s32,
float = f32) and wrapped values where it is not (U32, I64, U64, F64, Bin, HashDict), so dumps(loads(x)) keeps every
type. dumps writes what the game's own files hold: key and string tables sorted, hash entries sorted by key.
"""
import struct


class U32(int): pass
class I64(int): pass
class U64(int): pass
class F64(float): pass
class Bin(bytes): pass


class HashDict(dict):
    """0x20 / 0x21 hash nodes: int keys. width: 4 or 8 bytes."""
    def __init__(self, *a, width=4, **k):
        super().__init__(*a, **k)
        self.width = width


def loads(data):
    e = "<" if data[:2] == b"YB" else ">" if data[:2] == b"BY" else None
    if e is None:
        raise ValueError("not BYML")
    version, key_off, str_off, root_off = struct.unpack_from(e + "HIII", data, 2)
    order = "little" if e == "<" else "big"
    u32 = lambda o: struct.unpack_from(e + "I", data, o)[0]
    u24 = lambda o: int.from_bytes(data[o:o + 3], order)

    def strtab(off):
        if not off:
            return []
        n = u24(off + 1)
        offs = [u32(off + 4 + 4 * i) for i in range(n + 1)]
        return [data[off + offs[i]:data.index(b"\0", off + offs[i])].decode() for i in range(n)]

    keys, strs = strtab(key_off), strtab(str_off)

    def value(t, v):
        if t == 0xA0: return strs[v]
        if t == 0xA1: return Bin(data[v + 4:v + 4 + u32(v)])
        if t == 0xD0: return bool(v)
        if t == 0xD1: return struct.unpack(e + "i", struct.pack(e + "I", v))[0]
        if t == 0xD2: return struct.unpack(e + "f", struct.pack(e + "I", v))[0]
        if t == 0xD3: return U32(v)
        if t == 0xD4: return I64(struct.unpack_from(e + "q", data, v)[0])
        if t == 0xD5: return U64(struct.unpack_from(e + "Q", data, v)[0])
        if t == 0xD6: return F64(struct.unpack_from(e + "d", data, v)[0])
        if t == 0xFF: return None
        if t in (0xC0, 0xC1, 0x20, 0x21): return node(t, v)
        raise ValueError(f"value type {t:#x}")

    def node(t, off):
        n = u24(off + 1)
        if t == 0xC0:
            types = data[off + 4:off + 4 + n]
            base = off + 4 + ((n + 3) & ~3)
            return [value(types[i], u32(base + 4 * i)) for i in range(n)]
        if t == 0xC1:
            out = {}
            for i in range(n):
                p = off + 4 + 8 * i
                out[keys[u24(p)]] = value(data[p + 3], u32(p + 4))
            return out
        hw = 4 if t == 0x20 else 8
        ent = hw + 4
        types = data[off + 4 + n * ent:off + 4 + n * ent + n]
        out = HashDict(width=hw)
        for i in range(n):
            p = off + 4 + ent * i
            out[int.from_bytes(data[p:p + hw], order)] = value(types[i], u32(p + hw))
        return out

    root = node(data[root_off], root_off) if root_off else None
    return root, {"endian": e, "version": version}


def _type(v):
    if v is None: return 0xFF
    if isinstance(v, bool): return 0xD0
    if isinstance(v, HashDict): return 0x20 if v.width == 4 else 0x21
    if isinstance(v, dict): return 0xC1
    if isinstance(v, list): return 0xC0
    if isinstance(v, str): return 0xA0
    if isinstance(v, Bin): return 0xA1
    if isinstance(v, U32): return 0xD3
    if isinstance(v, I64): return 0xD4
    if isinstance(v, U64): return 0xD5
    if isinstance(v, F64): return 0xD6
    if isinstance(v, int): return 0xD1
    if isinstance(v, float): return 0xD2
    raise TypeError(type(v))


def dumps(root, endian="<", version=7):
    keys, strs = set(), set()

    def collect(v):
        if isinstance(v, HashDict):
            for x in v.values(): collect(x)
        elif isinstance(v, dict):
            for k, x in v.items():
                keys.add(k); collect(x)
        elif isinstance(v, list):
            for x in v: collect(x)
        elif isinstance(v, str):
            strs.add(v)
    collect(root)
    key_list = sorted(keys, key=lambda s: s.encode())
    str_list = sorted(strs, key=lambda s: s.encode())
    key_idx = {k: i for i, k in enumerate(key_list)}
    str_idx = {s: i for i, s in enumerate(str_list)}
    e = endian
    order = "little" if e == "<" else "big"
    u24 = lambda n: n.to_bytes(3, order)

    def strtab(lst):
        if not lst:
            return b""
        blobs = [s.encode() + b"\0" for s in lst]
        head = 4 + 4 * (len(lst) + 1)
        offs, pos = [], head
        for b in blobs:
            offs.append(pos); pos += len(b)
        offs.append(pos)
        out = bytes([0xC2]) + u24(len(lst)) + b"".join(struct.pack(e + "I", o) for o in offs) + b"".join(blobs)
        return out + b"\0" * (-len(out) % 4)

    ktab, stab = strtab(key_list), strtab(str_list)
    hdr = 0x10
    key_off = hdr if ktab else 0
    str_off = hdr + len(ktab) if stab else 0
    buf = bytearray(b"\0" * (hdr + len(ktab) + len(stab)))
    buf[hdr:hdr + len(ktab)] = ktab
    buf[hdr + len(ktab):hdr + len(ktab) + len(stab)] = stab

    def align(n=4):
        buf.extend(b"\0" * (-len(buf) % n))

    def field(v):
        """the u32 a container holds for v (writing v's own data first when it lives elsewhere)."""
        t = _type(v)
        if t in (0xC0, 0xC1, 0x20, 0x21): return write_node(v)
        if t == 0xA0: return str_idx[v]
        if t == 0xA1:
            align(4); off = len(buf); buf.extend(struct.pack(e + "I", len(v)) + bytes(v)); return off
        if t == 0xD0: return int(v)
        if t == 0xD1: return struct.unpack(e + "I", struct.pack(e + "i", v))[0]
        if t == 0xD2: return struct.unpack(e + "I", struct.pack(e + "f", v))[0]
        if t == 0xD3: return int(v)
        if t in (0xD4, 0xD5, 0xD6):
            align(4); off = len(buf)
            buf.extend(struct.pack(e + {0xD4: "q", 0xD5: "Q", 0xD6: "d"}[t], v)); return off
        if t == 0xFF: return 0

    def write_node(v):
        t = _type(v)
        if t == 0xC0:
            n = len(v)
            size = 4 + ((n + 3) & ~3) + 4 * n
        elif t == 0xC1:
            n = len(v); size = 4 + 8 * n
        else:
            n = len(v); size = 4 + (v.width + 4) * n + n
        align(4)
        off = len(buf)
        buf.extend(b"\0" * size)
        buf[off:off + 4] = bytes([t]) + u24(n)
        if t == 0xC0:
            buf[off + 4:off + 4 + n] = bytes(_type(x) for x in v)
            base = off + 4 + ((n + 3) & ~3)
            for i, x in enumerate(v):
                struct.pack_into(e + "I", buf, base + 4 * i, field(x))
        elif t == 0xC1:
            for i, k in enumerate(sorted(v, key=lambda k: key_idx[k])):
                p = off + 4 + 8 * i
                buf[p:p + 4] = u24(key_idx[k]) + bytes([_type(v[k])])
                struct.pack_into(e + "I", buf, p + 4, field(v[k]))
        else:
            w = v.width
            items = sorted(v.items())
            for i, (h, x) in enumerate(items):
                p = off + 4 + (w + 4) * i
                buf[p:p + w] = h.to_bytes(w, order)
                struct.pack_into(e + "I", buf, p + w, field(x))
            buf[off + 4 + (w + 4) * n:off + 4 + (w + 4) * n + n] = bytes(_type(x) for _, x in items)
        return off

    root_off = write_node(root) if root is not None else 0
    buf[0:2] = b"YB" if e == "<" else b"BY"
    struct.pack_into(e + "HIII", buf, 2, version, key_off, str_off, root_off)
    align(4)
    return bytes(buf)


# ---- SARC writing ----
def _sarc_hash(name, key=0x65):
    h = 0
    for c in name.encode():
        h = (h * key + (c - 256 if c > 127 else c)) & 0xFFFFFFFF
    return h


def build_sarc(files, align=8, data_align=None):
    """files: {name: bytes}. Nodes sorted by name hash, as the game's packs are. Each file starts on `align` (or on
    data_align[name] when given), the data section on the largest alignment used."""
    data_align = data_align or {}
    names = sorted(files, key=_sarc_hash)
    sfnt = bytearray()
    name_off = {}
    for n in names:
        name_off[n] = len(sfnt)
        sfnt.extend(n.encode() + b"\0")
        sfnt.extend(b"\0" * (-len(sfnt) % 4))
    head = 0x14 + 0x0C + 16 * len(names) + 8 + len(sfnt)
    biggest = max([align] + list(data_align.values()))
    data_off = head + (-head % biggest)
    blob = bytearray()
    spans = {}
    for n in names:
        a = data_align.get(n, align)
        blob.extend(b"\0" * (-len(blob) % a))
        spans[n] = (len(blob), len(blob) + len(files[n]))
        blob.extend(files[n])
    out = bytearray()
    out += b"SARC" + struct.pack("<HHIIHH", 0x14, 0xFEFF, data_off + len(blob), data_off, 0x100, 0)
    out += b"SFAT" + struct.pack("<HHI", 0x0C, len(names), 0x65)
    for n in names:
        out += struct.pack("<IIII", _sarc_hash(n), 0x01000000 | (name_off[n] // 4), *spans[n])
    out += b"SFNT" + struct.pack("<HH", 8, 0) + sfnt
    out += b"\0" * (data_off - len(out))
    out += blob
    return bytes(out)


def sarc_alignments(data):
    """{name: the largest power-of-two alignment its start (from the file's beginning) has, capped at 0x1000}."""
    from nxfmt import parse_sarc
    data_off = struct.unpack_from("<I", data, 0x0C)[0]
    count = struct.unpack_from("<H", data, 0x1A)[0]
    names = list(parse_sarc(data))
    out = {}
    for i in range(count):
        _, attr, start, _ = struct.unpack_from("<IIII", data, 0x20 + i * 16)
        sfnt = 0x20 + count * 16 + 8
        noff = sfnt + (attr & 0xFFFFFF) * 4
        name = data[noff:data.index(b"\0", noff)].decode()
        pos = data_off + start
        a = 1
        while a < 0x1000 and pos % (a * 2) == 0:
            a *= 2
        out[name] = a
    return out
