"""Rebuild a chest's candidate table + generic pool for any character (guest fns 823A9CD0, 823C98B0)."""
from chest_watch import BASE, category, predict

# global, category, stride, pool-id field, enabled field, char-mask field
POOL_DBS = [(0x8291D970, 3, 0x96, 0x00, 0x02, 0x34), (0x8291D984, 4, 0x4E, 0x08, 0x0A, 0x10),
            (0x8291D96C, 5, 0x46, 0x3C, 0x3E, 0x1E), (0x8291D994, 6, 0x3C, 0x36, 0x38, 0x08),
            (0x8291D97C, 7, 0x3E, 0x34, 0x36, 0x2A), (0x8291D98C, 12, 0x3C, 0x32, 0x34, 0x28),
            (0x8291D9A4, 13, 0x3A, 0x08, 0x0A, None)]
MASK = {cat: (g, stride, mf) for g, cat, stride, _, _, mf in POOL_DBS}
MASK.update({8: (0x8291D988, 0x3A, 0x2A), 9: (0x8291D980, 0x3E, 0x2A),   # 9 = glyphs
             10: (0x8291D99C, 0x3E, 0x2A), 11: (0x8291D978, 0x3E, 0x2A)})
# Character ids, from each character's exclusive weapons in the WEPN masks
CHARS = {1: 'Soma', 2: 'Alucard', 3: 'Jonathan', 4: 'Julius', 5: 'Richter', 6: 'Shanoa',
         7: 'Charlotte', 8: 'Yoko', 9: 'Maria'}


def db_files(m, g):
    o = m.u32(g)
    f = m.u32(m.u32(o + 4) + 0x10)
    yield f, 0, m.u32(f + 8)
    for e in range(m.u32(o + 0x10), m.u32(o + 0x14), 0x3C):
        h = m.u32(e + 0x2C)
        f = m.u32(h + 0x10) if h else 0
        if f:
            yield f, m.u32(f + 0xC), m.u32(f + 8)


def record(m, g, stride, idx):
    for f, first, count in db_files(m, g):
        if first <= idx < first + count:
            return f + 0x14 + (idx - first) * stride
    return None


def allowed(m, item, char):
    cat, idx = category(item), item - BASE[category(item)]
    if cat == 13:
        return idx != 0x3E or char in (8, 9)
    if cat not in MASK:
        return True
    g, stride, mf = MASK[cat]
    rec = record(m, g, stride, idx)
    return bool(rec and m.u32(rec + mf) >> (char - 1) & 1)


def build(m, table, pool_ids, char):
    """table: [(cat<<16|idx, weight)] from the chest record; returns (count, entries) like the chest object."""
    entries = []
    for packed, w in table:
        item = BASE[packed >> 16] + (packed & 0xFFFF)
        if w and allowed(m, item, char):
            entries.append((item, w * 10))
    count = len(entries)
    for pid in pool_ids:
        for g, cat, stride, pf, ef, mf in POOL_DBS:
            top = max(first + count for _, first, count in db_files(m, g))
            for i in range(top):
                rec = record(m, g, stride, i)
                if rec and m.s16(rec + pf) == pid and m.s16(rec + ef) and allowed(m, BASE[cat] + i, char):
                    entries.append((BASE[cat] + i, 0))
    return count, entries


def predict_for(m, table, pool_ids, char, rng, luck, acc_a=0, acc_b=0):
    count, entries = build(m, table, pool_ids, char)
    return predict({'count': count, 'entries': entries}, rng, luck, acc_a, acc_b)
