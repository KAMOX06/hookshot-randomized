"""MSBT message files (the game's: UTF-8, sections LBL1 and TXT2 only) reading and writing, to add messages.

Layout: header 0x20 ("MsgStdBn", BOM FF FE, encoding byte 0 = UTF-8 at +0x0C, version 3, section count u16 at +0x0E,
file size u32 at +0x12); sections: magic, size u32, 8 bytes pad, data, padded with 0xAB to 16.
LBL1: bucket count N, N x (label count, offset); labels: length u8, name, message index u32. A label's bucket is
(h = h * 0x492 + byte for each byte) mod N. TXT2: message count, offsets, NUL-terminated messages.
Tags: 0x0E, group u16, type u16, size u16, params. The game's "number from game data" tag is group 2 type 1:
params u16 name length, the GameData name, FF FF 00 (Npc_SkullDad:Talk_116 shows PouchData.Item_Token_Gold.StockNum).
"""
import struct


def _sections(d):
    n = struct.unpack_from("<H", d, 0x0E)[0]
    pos, out = 0x20, []
    for _ in range(n):
        magic, size = d[pos:pos + 4], struct.unpack_from("<I", d, pos + 4)[0]
        out.append((magic, d[pos + 0x10:pos + 0x10 + size]))
        pos += 0x10 + size + (-size % 16)
    return out


def label_hash(name, buckets):
    h = 0
    for c in name.encode():
        h = (h * 0x492 + c) & 0xFFFFFFFF
    return h % buckets


def read(d):
    """(labels {name: index}, messages [bytes], bucket count, header bytes)."""
    assert d[:8] == b"MsgStdBn" and d[8:10] == b"\xff\xfe" and d[0x0C] == 0, "not a UTF-8 little-endian msbt"
    sec = dict(_sections(d))
    assert set(sec) == {b"LBL1", b"TXT2"}, sec.keys()
    lbl, txt = sec[b"LBL1"], sec[b"TXT2"]
    n = struct.unpack_from("<I", lbl, 0)[0]
    labels = {}
    for i in range(n):
        cnt, off = struct.unpack_from("<II", lbl, 4 + 8 * i)
        for _ in range(cnt):
            L = lbl[off]
            labels[lbl[off + 1:off + 1 + L].decode()] = struct.unpack_from("<I", lbl, off + 1 + L)[0]
            off += 5 + L
    m = struct.unpack_from("<I", txt, 0)[0]
    offs = [struct.unpack_from("<I", txt, 4 + 4 * i)[0] for i in range(m)] + [len(txt)]
    msgs = [txt[offs[i]:offs[i + 1]] for i in range(m)]
    return labels, msgs, n, d[:0x20]


def _section(magic, data):
    out = magic + struct.pack("<I", len(data)) + b"\0" * 8 + data
    return out + b"\xab" * (-len(out) % 16)


def write(labels, msgs, buckets, header):
    groups = [[] for _ in range(buckets)]
    for name, idx in labels.items():   # (labels in a bucket in the order the game's are: by index)
        groups[label_hash(name, buckets)].append((idx, name))
    head = struct.pack("<I", buckets)
    body = b""
    base = 4 + 8 * buckets
    for g in groups:
        g.sort()
        head += struct.pack("<II", len(g), base + len(body))
        for idx, name in g:
            body += bytes([len(name)]) + name.encode() + struct.pack("<I", idx)
    lbl = head + body
    offs, blob = [], b""
    first = 4 + 4 * len(msgs)
    for m in msgs:
        offs.append(first + len(blob))
        blob += m
    txt = struct.pack("<I", len(msgs)) + b"".join(struct.pack("<I", o) for o in offs) + blob
    out = bytearray(header) + _section(b"LBL1", lbl) + _section(b"TXT2", txt)
    struct.pack_into("<I", out, 0x12, len(out))
    return bytes(out)


def gamedata_int_tag(name):
    """The tag showing an Int game data value (as the game's Gold Skulltula count)."""
    p = struct.pack("<H", len(name)) + name.encode() + b"\xff\xff\x00"
    return b"\x0e" + struct.pack("<HHH", 2, 1, len(p)) + p


def add_messages(d, new):
    """msbt data with messages label -> bytes (without the final NUL) added (labels must be new)."""
    labels, msgs, buckets, header = read(d)
    for name, text in new.items():
        assert name not in labels, f"{name} already there"
        labels[name] = len(msgs)
        msgs.append(text + b"\0")
    return write(labels, msgs, buckets, header)
