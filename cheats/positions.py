"""Cheat: save and restore your character's position in a stage. Unlike the predictor, this writes
to the game.

  python3 cheats/positions.py save Ch10_start     # remember where you are now
  python3 cheats/positions.py go Ch10_start       # move back there
  python3 cheats/positions.py up 1000             # move up (or down with a negative number)
  python3 cheats/positions.py right 50            # move right (or left with a negative number)
  python3 cheats/positions.py list                # saved positions
  python3 cheats/positions.py where               # where you are now

Positions are saved to cheats/positions.json, next to this script. Past the top of a stage the game
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

SAVED = os.path.join(HERE, 'positions.json')
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
    if sys.platform == 'win32':
        raise SystemExit('moving is only supported on Linux for now')
    path = next(l.split()[5] for l in open(f'/proc/{m.pid}/maps') if 'xenia_memory' in l)
    with open(path, 'r+b') as fh:
        for offs, vals in ((X_COPIES, pos['x']), (Y_COPIES, pos['y'])):
            for off, v in zip(offs, vals):
                fh.seek(m.off(obj + off, 4))
                fh.write(struct.pack('>f', v))


def load():
    try:
        with open(SAVED, encoding='utf-8') as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def main():
    cmd, name = (sys.argv[1:3] + [None, None])[:2]
    saved = load()
    if cmd == 'list':
        for n, p in saved.items():
            print(f'{n}: X {p["x"][0]:.1f}  Y {p["y"][0]:.1f}  ({p.get("char", "?")})')
        return
    m = Mem()
    obj = me(m)
    import pool
    char = pool.CHARS.get(cw.character_id(m, obj), '?')
    if cmd == 'where':
        p = read_pos(m, obj)
        print(f'{char}: X {p["x"][0]:.1f}  Y {p["y"][0]:.1f}')
    elif cmd == 'save' and name:
        saved[name] = {**read_pos(m, obj), 'char': char}
        with open(SAVED, 'w', encoding='utf-8') as fh:
            json.dump(saved, fh, indent=1)
        print(f'saved {name}: X {saved[name]["x"][0]:.1f}  Y {saved[name]["y"][0]:.1f} ({char})')
    elif cmd in ('up', 'right') and name:
        try:
            step = float(name)
        except ValueError:
            raise SystemExit(f'{cmd} takes a number, like: positions.py {cmd} 1000')
        p = read_pos(m, obj)
        if cmd == 'up':         # Y counts downward
            p['y'] = [v - step for v in p['y']]
        else:
            p['x'] = [v + step for v in p['x']]
        write_pos(m, obj, p)
        print(f'moved {cmd} {step:g}: X {p["x"][0]:.1f}  Y {p["y"][0]:.1f}')
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
