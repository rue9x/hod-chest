"""Cheat: make the stage's normal chests give one item. Unlike the predictor, this writes to the game.

  python3 cheats/loot.py set "Muramasa +1"          # every unopened normal chest now gives it
  python3 cheats/loot.py set "Muramasa +1" --all    # the boss and gold chests too
  python3 cheats/loot.py reset                      # put back what they held

Normal chests are every chest except the boss chest and gold chests. Each chest owns its list of
(item, weight) entries, and the roll only ever picks from that list, so setting every entry's item
makes any roll give it. The original items are saved to cheats/loot_backup.json first, for `undo`
(which only works in the same stage, before those chests are opened).
"""
import json, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))      # the predictor's modules, one folder up
import chest_watch as cw                        # noqa: E402
from gmem import Mem                            # noqa: E402

import paths                                    # noqa: E402
BACKUP = paths.cheat_file('loot_backup.json')   # next to the .exe when packaged
SKIP_TYPES = {4, 2}     # boss chest, gold chest (cw.CHEST_TYPES)


def game_file(m):
    if sys.platform == 'win32':
        raise SystemExit('changing chests is only supported on Linux for now')
    return next(l.split()[5] for l in open(f'/proc/{m.pid}/maps') if 'xenia_memory' in l)


def chests(m, everything=False):
    """[(chest address, [entry pair addresses])] for every unopened chest in the stage: normal ones,
    or with `everything` the boss and gold chests too."""
    out = []
    for h in cw.scan_chests(m, 0x40000000, 0x50000000):
        try:
            if m.u32(h) != cw.TREASURE_VTABLE:
                continue
            c = cw.read_chest(m, h)
            if c['state'] in cw.OPENED_STATES or (c['type'] in SKIP_TYPES and not everything):
                continue
            b, e = m.u32(h + 0x10C), m.u32(h + 0x110)
            pairs = [q for q in (m.u32(p) for p in range(b, e, 4)) if q]
        except ValueError:
            continue
        if pairs:
            out.append((h, pairs))
    return out


def item_id(name, names):
    want = cw.squash(name)
    ids = [k for k, v in names.items() if cw.squash(v) == want]
    if not ids:
        close = [v for v in names.values() if want in cw.squash(v)][:5]
        raise ValueError(f'no item named {name!r}' + (f' (did you mean: {", ".join(close)}?)' if close else ''))
    return ids[0]


def set_loot(m, name, everything=False):
    """Make every unopened chest (normal ones, or `everything`) give the item `name`. Returns a message."""
    names = cw.load_names()
    item = item_id(name, names)
    found = chests(m, everything)
    if not found:
        raise ValueError('no unopened chests in this stage')
    try:                        # keep the first originals: a second set mustn't save its own items
        with open(BACKUP, encoding='utf-8') as fh:
            backup = json.load(fh)
    except (FileNotFoundError, ValueError):
        backup = {}
    for _, pairs in found:
        for q in pairs:
            backup.setdefault(f'{q:#x}', m.u32(q))
    with open(BACKUP, 'w', encoding='utf-8') as fh:
        json.dump(backup, fh)
    with open(game_file(m), 'r+b') as fh:
        for _, pairs in found:
            for q in pairs:
                fh.seek(m.off(q, 4))
                fh.write(struct.pack('>I', item))
    which = 'chests' if everything else 'normal chests'
    return f'{len(found)} {which} now drop {names.get(item, hex(item))}'


def reset(m):
    """Put back what the chests held before set_loot. Returns a message."""
    try:
        with open(BACKUP, encoding='utf-8') as fh:
            backup = json.load(fh)
    except FileNotFoundError:
        return 'Nothing to reset: the chests hold what the game gave them.'
    put = 0
    with open(game_file(m), 'r+b') as fh:
        for q, item in backup.items():
            try:
                fh.seek(m.off(int(q, 16), 4))
            except ValueError:
                continue            # that chest's memory is gone (new stage)
            fh.write(struct.pack('>I', item))
            put += 1
    os.remove(BACKUP)
    return f'Chests reset ({put} entries put back).'


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    m = Mem()
    try:
        if cmd == 'set' and len(sys.argv) > 2:
            print(set_loot(m, sys.argv[2], everything='--all' in sys.argv[3:]))
        elif cmd in ('undo', 'reset'):
            print(reset(m))
        else:
            raise SystemExit(__doc__)
    except ValueError as e:
        raise SystemExit(str(e))


if __name__ == '__main__':
    main()
