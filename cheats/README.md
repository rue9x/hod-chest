# Cheats

Tools that **write to the game**, kept apart from the predictor, which only ever reads it.
Use them solo: in co-op your game sends your position to the others, and a write can desync the
session. They work on Linux (tested with the game) and Windows (written with `WriteProcessMemory`;
tested under Wine, not yet with the game on Windows).

## Positions (`positions.py`)

Save where your character stands and move back there, or move by a set amount.

```
python3 cheats/positions.py save ch10_start   # remember where you are now
python3 cheats/positions.py go ch10_start     # move back there
python3 cheats/positions.py list              # saved positions
python3 cheats/positions.py where             # where you are now
```

On the web page (`chest_watch.py --serve`, or the sidebar's http://localhost:8765/), the **Cheats**
section lists every saved position: pick one and press **Activate** to move there. Save new ones
with the `save` command above; the list picks them up within a few seconds.

Saved positions go in `cheats/positions.json`, which stays on your computer (it's in `.gitignore`).
A position only means something in the stage it was saved in.

What was found by testing in Chapter 10 with Soma:

- X grows to the right. Y grows **downward**, so "up" lowers it.
- The game keeps six copies of each, and puts the first back from the others within a frame, so
  all twelve values are written together. That sticks, and normal physics carries on from there.
- Moving into open air inside the stage, you fall and land as usual. Past the top of the stage
  (between 3000 and 4000 above the Chapter 10 start), gravity stops and the game carries you off
  to the left. `go` brings you back.
- Moving into a wall or ceiling can leave you stuck; `go` gets you out.

## Chest loot (`loot.py`)

Make every unopened normal chest in the stage give one item: every chest except the boss chest and
gold chests (or those too, with `--all`).

```
python3 cheats/loot.py set "Muramasa +1"          # every normal chest now gives it
python3 cheats/loot.py set "Muramasa +1" --all    # the boss and gold chests too
python3 cheats/loot.py reset                      # put back what they held (same stage, unopened)
```

On the page, **Chest loot** does the same: pick **All normal chests drop X** or **All chests drop X**,
type in **Filter items** to narrow the item list, pick the item, and press **Activate**.
**Reset chests** puts the original items back.

Each chest owns its own list of (item, weight) entries, and opening it rolls from that list, so
setting every entry's item makes any roll give it. Tested in Chapter 10: a blue chest gave
Muramasa +1. It lasts until the stage reloads, which rebuilds the chests. The original items are
saved to `cheats/loot_backup.json` (kept off git) for `reset`; setting another item keeps the
first originals, so one reset always goes back to what the game gave.

## Sub-weapon mastery (`mastery.py`)

Mastery is hidden points per sub-weapon, earned each time it hits (double on hard mode, more with
mastery rings). The bars are drawn from the game's limits: 0, 16, 64, 256, 1024, 4096, 16384,
32768, 99999 points for 0 to 8 full bars.

```
python3 cheats/mastery.py next    # wait for your next sub-weapon hit, then max that one (8 bars)
python3 cheats/mastery.py show    # every slot's points and bars
```

On the page, **Sub-weapon mastery → Activate** does the same: it waits up to a minute for a hit.
**Deactivate** calls it off while it waits, and nothing is changed.
It watches the whole points table and maxes the first slot that goes up, so it works for any
sub-weapon. Every character's sub-weapons share one table; tested and working for Jonathan
(slots 90-106), Julius (140 on), Richter (190-195), Simon (440-445),
Getsu Fuma (507-509), Yoko (340-342), Maria (390-394) and Shanoa (240-246). Found in Chapter 10 with Jonathan: setting the Bible's points to 99999 showed 8 bars, and
**Activate** then maxed the next sub-weapon used.

## Health and MP (`health.py`)

```
python3 cheats/health.py fill       # HP to your current max
python3 cheats/health.py fill mp    # MP to your current max
python3 cheats/health.py show       # HP and MP, with their max
```

On the page, **Health → Fill HP / Fill MP** do the same. HP and MP are floats in the player's stats
block; the game clamps each every frame to a base max plus the equipment bonus, so filling sets it
to exactly that. Found with Soma: HP 256 of 300 + 24 (writing 1000 settled at 324), and MP
refilling to 100 + 18.
