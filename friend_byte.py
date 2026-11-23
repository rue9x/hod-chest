"""Narrow down a friend's chest-seed byte from drops they reported.

  python3 friend_byte.py                       # show candidate bytes from all observations
  python3 friend_byte.py add Charlotte "Venus Brooch"   # record their drop from the latest logged boss chest

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
    try:
        return [json.loads(ln) for ln in open(CHESTS) if ln.strip()]
    except OSError:
        return []


def load_obs():
    try:
        return json.load(open(OBS))
    except (OSError, ValueError):
        return []


def main():
    names = cw.load_names()
    by_item = {v.lower(): k for k, v in names.items()}
    by_char = {v.lower(): k for k, v in pool.CHARS.items()}
    obs = load_obs()
    if len(sys.argv) >= 4 and sys.argv[1] == 'add':
        chests = load_chests()
        if not chests:
            raise SystemExit('no logged chests yet')
        obs.append({'chest': chests[-1], 'char': sys.argv[2], 'item': sys.argv[3]})
        json.dump(obs, open(OBS, 'w'), indent=1)
    m = Mem()
    per_char = {}
    for o in obs:
        cid, want = by_char[o['char'].lower()], by_item.get(o['item'].lower())
        if want is None:
            raise SystemExit(f'unknown item name: {o["item"]}')
        ok = {b for b in range(256) if want in cw.predict_other(m, o['chest'], cid, b, luck_steps=25)}
        per_char[o['char']] = per_char.get(o['char'], set(range(256))) & ok
    for ch, bytes_ in per_char.items():
        n = sum(1 for o in obs if o['char'] == ch)
        likely = sorted(b for b in bytes_ if (b & 0x1F) < 4)
        print(f'{ch}: {len(bytes_)} of 256 bytes fit {n} drop(s); low-slot candidates: '
              f'{", ".join(f"{b:#04x}" for b in likely) or "none"}')
        if len(bytes_) == 1:
            print(f'  -> put "{ch} {next(iter(bytes_)):#04x}" in friends.txt')


if __name__ == '__main__':
    main()
