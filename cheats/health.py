"""Cheat: fill your HP or MP. Unlike the predictor, this writes to the game.

  python3 cheats/health.py fill        # HP to your current max
  python3 cheats/health.py fill mp     # MP to your current max
  python3 cheats/health.py show        # HP and MP, with their max

HP and MP live in the player's stats block (the one the predictor reads Luck from) as floats. The
game clamps each to its max every frame: a base max plus a bonus (equipment). Found with Soma:
HP 256 of 300 + 24 (writing 1000 settled at 324), and MP refilling to 100 + 18.
"""
import os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))      # the predictor's modules, one folder up
import chest_watch as cw                        # noqa: E402
from gmem import Mem                            # noqa: E402

# stats-block offsets, big-endian floats: (value, base max, bonus)
STATS = {'hp': (0x04, 0x10, 0x1C), 'mp': (0x08, 0x14, 0x20)}


def stats(m):
    obj = next((o for _, o, loc in cw.players(m) if loc), None)
    if not obj:
        raise ValueError('no player loaded: enter a stage first')
    s = m.u32(m.u32(obj + 0x924) + 0x58)
    if not s:
        raise ValueError("can't find your stats yet")
    return s


def read(m, s, what='hp'):
    value, base, bonus = STATS[what]
    f = lambda off: struct.unpack('>f', m.read(s + off, 4))[0]
    return f(value), f(base) + f(bonus)


def fill(m, what='hp'):
    """Set HP or MP to its max (base + bonus). Returns a message."""
    what = what.lower()
    if what not in STATS:
        raise ValueError(f'fill takes hp or mp, not {what!r}')
    s = stats(m)
    now, top = read(m, s, what)
    if not 0 < top < 1e6:
        raise ValueError(f"max {what.upper()} reads as {top}; not touching it")
    m.write_f32(s + STATS[what][0], top)
    return f'{what.upper()} filled: {now:.0f} -> {top:.0f}.'


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    m = Mem()
    try:
        if cmd == 'fill':
            print(fill(m, sys.argv[2] if len(sys.argv) > 2 else 'hp'))
        elif cmd == 'show':
            s = stats(m)
            for what in STATS:
                now, top = read(m, s, what)
                print(f'{what.upper()} {now:.0f} / {top:.0f}')
        else:
            raise SystemExit(__doc__)
    except ValueError as e:
        raise SystemExit(str(e))


if __name__ == '__main__':
    main()
