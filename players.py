"""Show every player in the stage as the watcher sees them: character, Luck, luck accessories."""
import pool
import chest_watch as cw
from gmem import Mem

m = Mem()
ps = cw.players(m)
if not ps:
    print('no players loaded (not in a stage)')
for slot, obj, local in ps:
    cid = cw.character_id(m, obj)
    print(f'slot {slot} {"YOU   " if local else "remote"} {pool.CHARS.get(cid, cid)!s:<10} '
          f'LCK {cw.estimate_luck(m, obj)}  luck accessories {cw.estimate_accessories(m, obj)}')
