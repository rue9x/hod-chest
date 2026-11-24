"""Show every player in the stage as the watcher sees them: character, Luck, luck accessories."""
import pool
import chest_watch as cw
from gmem import Mem


def read(fn, *a):
    """A value read from the game, or 'unknown' when it isn't readable right now."""
    try:
        return fn(*a)
    except ValueError:
        return 'unknown'


m = Mem()
try:
    ps = cw.players(m)
except ValueError:      # outside a stage the player manager isn't there to read
    ps = []
if not ps:
    print('no players loaded (not in a stage)')
for slot, obj, local in ps:
    cid = read(cw.character_id, m, obj)
    print(f'slot {slot} {"YOU   " if local else "remote"} {pool.CHARS.get(cid, cid)!s:<10} '
          f'LCK {read(cw.estimate_luck, m, obj)}  luck accessories {read(cw.estimate_accessories, m, obj)}')
