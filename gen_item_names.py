"""Build item_names.json from the game's own item databases + decoded text resources.

Needs the game running in a stage: the item DBs and every DLC's textresNN file are
then in guest RAM. Glyphs (category 9) have no DB here; their names come from the
community list in build_names.py.
"""
import json, os, struct

from build_names import RAW
from chest_watch import BASE
from gmem import Mem
from txrc import decode_bytes

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'item_names.json')
TEXT_MGR = 0x8291D9DC   # -> {?, slots_begin, slots_end}; slot N = textresNN (0 = base game)
# DB global: (magic, item category, record stride, name-field offset, text section)
DBS = {0x8291D970: (b'WEPN', 3, 0x96, 0x3A, 0), 0x8291D984: (b'GURD', 4, 0x4E, 0x02, 2),
       0x8291D96C: (b'SWPN', 5, 0x46, 0x24, 13), 0x8291D994: (b'SOUL', 6, 0x3C, 0x02, 13),
       0x8291D97C: (b'DKMG', 7, 0x3E, 0x02, 13), 0x8291D98C: (b'MART', 12, 0x3C, 0x02, 13),
       0x8291D9A4: (b'USEI', 13, 0x3A, 0x02, 4)}


def community():
    out = {}
    for entry in RAW.replace('\n', '|').split('|'):
        if entry.strip():
            cat, idx, name = entry.strip().split(' ', 2)
            out[BASE[int(cat, 16)] + int(idx, 16)] = name
    return out


def text_slots(m):
    mgr = m.u32(TEXT_MGR)
    b, e = m.u32(mgr + 4), m.u32(mgr + 8)
    slots = {}
    for i, p in enumerate(range(b, e, 4)):
        try:                    # one unreadable resource shouldn't stop the rest
            slot = m.u32(p)
            if slot and m.u32(slot + 4) and m.read(m.u32(slot + 4), 4) == b'TXRC':
                slots[i] = decode_bytes(read_text(m, m.u32(slot + 4)))
        except (ValueError, IndexError, struct.error):
            continue
    return slots


def read_text(m, f):
    """A whole text resource, sized from its own tables wherever they sit: the string data ends at
    the furthest entry (each entry packs a length and an offset into the data)."""
    nsec_off, sec_off, ent_off, data_off = struct.unpack('>4I', m.read(f + 0xC, 16))
    nsec = m.u16(f + nsec_off)
    secs = [struct.unpack('>HH', m.read(f + sec_off + 4 * s, 4)) for s in range(nsec)]
    last = max((start + cnt for cnt, start in secs), default=0)        # entries used: 0 .. last-1
    ents = m.read(f + ent_off, 4 * last) if last else b''
    end = max(data_off, nsec_off + 2, sec_off + 4 * nsec, ent_off + 4 * last)
    for i in range(last):
        w = struct.unpack('>I', ents[4 * i:4 * i + 4])[0]
        end = max(end, data_off + (w & 0x3FFFFF) + (w >> 22))
    if end > 1 << 24:
        raise ValueError('text resource tables look wrong')
    return m.read(f, end)


def db_files(m, g):
    """Yield (file, dlc number) for the base DB and each DLC DB under global g."""
    o = m.u32(g)
    yield m.u32(m.u32(o + 4) + 0x10), 0
    for e in range(m.u32(o + 0x10), m.u32(o + 0x14), 0x3C):
        h, f, dlc = 0, 0, -1
        try:                    # a DLC that isn't loaded (or owned): build() records it as missing
            dlc = m.u32(e)      # first, so a later failed read still names which DLC it was
            h = m.u32(e + 0x2C)
            f = m.u32(h + 0x10) if h else 0
        except ValueError:
            f = 0
        yield f, dlc            # h == 0 too: build() records it as missing, so a save waits on it


def build(m):
    """Return (names {flat id: name}, text slots found, problems)."""
    slots, names, problems = text_slots(m), community(), []
    for g, (magic, cat, stride, field, sec) in DBS.items():
        for f, dlc in db_files(m, g):
            try:
                if not f or m.read(f, 4) != magic:
                    problems.append(f'{magic.decode()} dlc{dlc}: no file')
                    continue
                count, first = m.u32(f + 8), m.u32(f + 0xC)
            except ValueError:
                if dlc == 0:        # the base game's database: not in a stage, say so to the caller
                    raise
                problems.append(f'{magic.decode()} dlc{dlc}: unreadable')
                continue
            text = slots.get(dlc)
            for i in range(count):
                try:
                    tid = struct.unpack('>H', m.read(f + 0x14 + i * stride + field, 2))[0]
                except ValueError:
                    problems.append(f'{magic.decode()} dlc{dlc} item {first + i:#x}: unreadable')
                    continue
                s = text[sec][tid] if text and sec < len(text) and tid < len(text[sec]) else None
                if s and s != b'NO_DATA':
                    nm = s.decode('utf-8', 'replace').replace('\n', ' ')
                    names[BASE[cat] + first + i] = nm + ' Soul' if magic == b'SOUL' else nm
                else:
                    problems.append(f'{magic.decode()} dlc{dlc} item {first + i:#x}: no text')
    return names, slots, problems


def write(names, path=OUT):
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump({f'0x{k:X}': v for k, v in sorted(names.items())}, fh, indent=1)


def main():
    try:
        names, slots, problems = build(Mem())
    except ValueError:
        raise SystemExit('enter a stage first: the item lists are read from the running game.')
    try:
        with open(OUT, encoding='utf-8') as fh:
            have = len(json.load(fh))
    except (OSError, ValueError):
        have = 0
    if len(names) <= 200 or len(names) < have:
        raise SystemExit(f'only {len(names)} names found (the file has {have}); left it alone. '
                         'Run this in a stage once everything has loaded.')
    write(names)
    print(f'{len(names)} names written to {OUT}; text files for DLC slots {sorted(slots)}')
    for p in problems:
        print('  ', p)


if __name__ == '__main__':
    main()
