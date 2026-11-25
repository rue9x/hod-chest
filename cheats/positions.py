"""Cheat: save and restore your character's position in a stage. Unlike the predictor, this writes
to the game.

  python3 cheats/positions.py save ch10_start     # remember where you are now
  python3 cheats/positions.py go ch10_start       # move back there
  python3 cheats/positions.py list                # saved positions
  python3 cheats/positions.py where               # where you are now

The chapter positions ship in cheats/positions_default.json; yours are saved to cheats/positions.json
(next to the .exe when packaged) and shown alongside them. Past the top of a stage the game
stops gravity and carries you off to the left; `go` brings you back. Use it solo: in co-op your game sends its
position to the others, and moving into a wall or out of the stage can leave you stuck.

The game keeps the position in several places in the player object; writing only the first snaps
back within a frame, so every copy is saved and written together.
"""
import json, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))      # the predictor's modules, one folder up
import chest_watch as cw                        # noqa: E402
from gmem import Mem                            # noqa: E402

import paths                                    # noqa: E402
SAVED = paths.cheat_file('positions.json')      # your own saves: next to the .exe when packaged
# The chapter start/end positions that ship with hod-chest (in the repo, and inside the .exe).
DEFAULTS = os.path.join(paths.RES, 'cheats', 'positions_default.json')
# Player-object offsets that hold the position (found by walking and jumping): X and Y, then copies.
X_COPIES = (0x40, 0x8D4, 0x8DC, 0x8EC, 0x8F4, 0xB8C)
Y_COPIES = (0x44, 0x8D8, 0x8E0, 0x8F0, 0x8F8, 0xB90)


def me(m):
    obj = next((o for _, o, loc in cw.players(m) if loc), None)
    if not obj:
        raise SystemExit('no player loaded: enter a stage first')
    return obj


def read_pos(m, obj):
    rd = lambda off: struct.unpack('>f', m.read(obj + off, 4))[0]
    return {'x': [rd(o) for o in X_COPIES], 'y': [rd(o) for o in Y_COPIES]}


def write_pos(m, obj, pos):
    """Write every copy, so the game doesn't put the old position back."""
    for offs, vals in ((X_COPIES, pos['x']), (Y_COPIES, pos['y'])):
        for off, v in zip(offs, vals):
            m.write_f32(obj + off, v)


def natural(name):
    """Sort key: numbers by value, so ch1 < ch9 < ch10, and start before end within a chapter."""
    import re
    parts = re.split(r'(\d+)', name)
    return [int(p) if p.isdigit() else {'start': 0, 'end': 1}.get(p.strip('_'), 2) if i == len(parts) - 1 else p
            for i, p in enumerate(parts)]


def read_json(path):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def load_own():
    """Only the positions you saved yourself (what `save` writes back)."""
    return read_json(SAVED)


def load():
    """Every position, sorted by chapter: the shipped defaults, then yours (yours win on a name)."""
    merged = {**read_json(DEFAULTS), **load_own()}
    return dict(sorted(merged.items(), key=lambda kv: natural(kv[0])))


def main():
    cmd, name = (sys.argv[1:3] + [None, None])[:2]
    if cmd in ('save', 'go') and name:
        name = name.lower()     # names are kept lowercase: ch10_start, ch11_end
    saved = load()
    if cmd == 'list':
        for n, p in saved.items():
            print(f'{n}: X {p["x"][0]:.1f}  Y {p["y"][0]:.1f}')
        return
    m = Mem()
    obj = me(m)
    if cmd == 'where':
        p = read_pos(m, obj)
        print(f'X {p["x"][0]:.1f}  Y {p["y"][0]:.1f}')
    elif cmd == 'save' and name:
        own = load_own()                    # never copy the shipped defaults into your file
        own[name] = read_pos(m, obj)        # any character can use it: the layout is the same
        with open(SAVED, 'w', encoding='utf-8') as fh:
            json.dump(own, fh, indent=1)
        print(f'saved {name}: X {own[name]["x"][0]:.1f}  Y {own[name]["y"][0]:.1f}')
    elif cmd == 'go' and name:
        if name not in saved:
            raise SystemExit(f'no saved position {name!r} (saved: {", ".join(saved) or "none"})')
        write_pos(m, obj, saved[name])
        p = read_pos(m, obj)
        print(f'moved to {name}: X {p["x"][0]:.1f}  Y {p["y"][0]:.1f}')
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main()
