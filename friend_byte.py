"""Narrow down a friend's chest-seed byte from drops they reported.

  python3 friend_byte.py                       # show candidate bytes from all observations
  python3 friend_byte.py add Charlotte "Venus Brooch"   # record their drop from the latest logged boss chest
  python3 friend_byte.py add Charlotte "Venus Brooch" SomeFriend   # ...and say which friend it was

A friend's byte is fixed only for one session, so drops are grouped by character and friend:
name the friend when two people play the same character, or after they rejoin.

Chest snapshots come from chests.jsonl (written by the logger / chest_watch). Needs the game
running (item databases are read from guest memory to rebuild each character's item list).
"""
import json, os, sys

import chest_watch as cw
import pool
from gmem import Mem

HERE = os.path.dirname(os.path.abspath(__file__))
CHESTS, OBS = os.path.join(HERE, 'chests.jsonl'), os.path.join(HERE, 'observations.json')


def load_chests():
    """Logged boss chests; a damaged line (the watcher killed mid-write) is skipped."""
    out = []
    try:
        with open(CHESTS, encoding='utf-8') as fh:
            for ln in fh:
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def load_obs():
    """Saved drop reports. A file that won't parse stops the script, so `add` never overwrites it."""
    try:
        with open(OBS, encoding='utf-8') as fh:
            return json.load(fh)
    except FileNotFoundError:
        return []
    except ValueError as e:
        raise SystemExit(f'{OBS} is damaged ({e}); fix or move it, then try again. Nothing was changed.')


def fits(c, chest, byte, items):
    """Whether seed byte `byte` gives any of `items` from table `c` at some Luck 0-300 and luck-item count."""
    st = cw.chest_state_from_seed((chest['base'] + chest['key'] + 1 + byte) & 0xFFFFFFFF)
    for a in range(3):
        for b in range(3):
            if not cw.wearable(a, b):   # more luck items than there are slots for
                continue
            for luck in range(301):
                if cw.predict(c, st, luck, a, b)[0] in items:
                    return True
    return False


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == 'add' and len(sys.argv) < 4:
        raise SystemExit('usage: friend_byte.py add CHARACTER "ITEM" [FRIEND]')
    m = Mem()
    names = cw.load_names()
    if len(names) <= 200:       # not built yet (it's made from your own game, not shipped)
        try:
            import gen_item_names
            built, _, problems = gen_item_names.build(m)
        except ValueError:
            built, problems = {}, []
        if len(built) <= 200:
            raise SystemExit('item names not built yet: enter a stage, then run this again '
                             '(or run chest_watch.py or gen_item_names.py in a stage first).')
        if not problems:        # a partial build is used for this run, but saving it is the watcher's call
            gen_item_names.write(built)
        names = built
    by_item = {}                # a name can belong to several items (two Simon's Cloaks)
    for k, v in names.items():
        by_item.setdefault(v.lower(), set()).add(k)
    by_char = {v.lower(): k for k, v in pool.CHARS.items()}
    obs = load_obs()
    if len(sys.argv) >= 4 and sys.argv[1] == 'add':
        chests = load_chests()
        if not chests:
            raise SystemExit('no logged chests yet')
        char, item = sys.argv[2], sys.argv[3]
        who = sys.argv[4].strip().lower() if len(sys.argv) > 4 else ''     # one group however it's typed
        # Check the names first: a saved typo would break every later run.
        if char.lower() not in by_char:
            raise SystemExit(f'unknown character: {char} (use one of {", ".join(pool.CHARS.values())})')
        if item.lower() not in by_item:
            close = [v for v in names.values() if cw.squash(item) in cw.squash(v)][:5]
            raise SystemExit(f'unknown item name: {item}' + (f' (did you mean: {", ".join(close)}?)' if close else ''))
        try:                    # item databases are only in memory during a stage
            pool.build(m, [tuple(t) for t in chests[-1]['table']], chests[-1]['pools'], by_char[char.lower()])
        except ValueError:
            raise SystemExit('enter a stage first: the item lists are read from the running game. Nothing was saved.')
        obs.append({'chest': chests[-1], 'char': pool.CHARS[by_char[char.lower()]], 'item': item, 'who': who})
        with open(OBS, 'w', encoding='utf-8') as fh:
            json.dump(obs, fh, indent=1)
    per_char, used = {}, {}
    for o in obs:
        cid, want = by_char.get(o['char'].lower()), by_item.get(o['item'].lower())
        if cid is None or want is None:
            print(f'skipping a saved drop with an unknown name: {o["char"]} / {o["item"]}')
            continue
        # one group per character and friend, whatever case the character was typed in
        ch = pool.CHARS[cid] + (f' ({o["who"].strip().lower()})' if o.get('who') else '')
        try:
            count, entries = pool.build(m, [tuple(t) for t in o['chest']['table']], o['chest']['pools'], cid)
        except ValueError:
            raise SystemExit('enter a stage first: the item lists are read from the running game.')
        c = {'count': count, 'entries': list(entries)}      # the same for every byte
        ok = {b for b in range(256) if fits(c, o['chest'], b, want)}
        per_char[ch] = per_char.get(ch, set(range(256))) & ok
        used[ch] = used.get(ch, 0) + 1
    for ch, bytes_ in per_char.items():
        n = used[ch]
        likely = sorted(b for b in bytes_ if (b & 0x1F) < 6)   # seats 0-5: the game's six player slots
        print(f'{ch}: {len(bytes_)} of 256 bytes fit {n} drop(s); low-slot candidates: '
              f'{", ".join(f"{b:#04x}" for b in likely) or "none"}')
        if len(bytes_) == 1:
            print(f"  -> run chest_watch.py --friend '{ch.split(' (')[0]}:{next(iter(bytes_)):#04x}' "
                  "if the game can't read the session")


if __name__ == '__main__':
    main()
