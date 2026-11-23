# HoD Boss Chest Predictor

Predicts what the boss chest will drop in **Castlevania: Harmony of Despair Recomp** before you
open it, on Linux or Windows. In co-op it also predicts each friend's drop. It reads the game's
memory live and replays the game's own drop roll. It never writes to the game.

```
BOSS chest @ 412F5F70  state 2  rng[21]=617BB678  LCK 33 acc 1/0 (estimated from player)
  >>> WILL DROP: 0004:00EF Benten Raiment  via pool[10/11]  (roll 3517/10000)
      000D:0022 Super Potion                   w=50      0.5%
      ...
  friend Richter [byte 0x21, LCK 6, acc 0]: WILL DROP 0004:0053 Megingiord
```

## Requirements

- The Recomp, running as `harmonyrecomp` or `cvhd` (`.exe` on Windows)
- Python 3.9 or newer, with no extra packages

No game assets or binaries are included. Everything is read from your own running game.

| OS | How it reads the game | Status |
|---|---|---|
| Linux | maps the emulator's `/dev/shm/xenia_memory_*` file; no root or ptrace needed | tested |
| Windows | `ReadProcessMemory` through Python's built-in `ctypes`; run it as the same user as the game, no admin needed | **untested** |

On Windows, use `py` or `python` wherever this README says `python3`. The live view works in
Windows Terminal and in the classic console, and item names print as UTF-8 either way.

## Use

```
python3 chest_watch.py           # live view; refreshes when anything changes
python3 chest_watch.py --once    # one snapshot
python3 chest_watch.py --all     # every unopened chest, not just the boss chest
python3 players.py               # each player's detected character, Luck and luck accessories
```

The boss chest shows up as soon as the stage loads. The prediction becomes final when you touch
the chest, which is when the game locks in your Luck. Equipment changes before then can change it.

### Co-op (`friends.txt`)

Each player rolls their own chest. The seed is the stage's shared seed plus that player's
connection byte. `friends.txt` has one line per friend:

```
auto 0x21              # character auto-detected; Luck and accessories read from the game
Charlotte 0x21 luck=4  # or set any of them by hand
```

The first friend is usually `0x21`, the next `0x22`, and so on. If a friend's real drop doesn't
match, record it and the tool narrows down the right byte:

```
python3 friend_byte.py add Richter "Megingiord"
```

Boss chests are saved to `chests.jsonl` automatically for this.

### Rerolling for a target (`want.txt`)

The stage seed is the game's uptime in milliseconds at the moment the stage loads. A one-millisecond
difference gives a completely different drop, so you can't time the "Start the Game" press. What you
*can* do is reload and let the watcher tell you which loads are worth opening.

Name the items you want with `--want` (repeatable) or in `want.txt`, one per line. Names match as
case-insensitive substrings, so `valmanway` also matches `Valmanway +1`.

```
python3 chest_watch.py --want "Miser Ring" --want valmanway
```

The top line of each frame then says what to do:

| Top line | Meaning |
|---|---|
| `*** TARGET HIT` | Your drop is a target. Open the chest. The terminal bell rings once per load. |
| `+++ TARGET REACHABLE` | Not as you are, but this load's seed gives a target at another Luck or luck-item count. Change to one of the listed setups **before** touching the chest and wait for `TARGET HIT`. |
| `??? TARGET POSSIBLE` | A friend might get a target, depending on their Luck. |
| `--- no target` | Nothing you can wear helps this load. Reload. |

Each line also counts the stage loads seen so far.

A `REACHABLE` setup reads like `Valmanway: LCK 21-33 with acc 0/0`: a Luck range plus how many
type-A / type-B luck items to wear. Type A is Thief Ring or Ebisu Raiment, and type B is Soul Eater
Ring. Setups closest to your current gear come first. Only setups you can actually build are shown:

- **Luck cap:** at most your current LCK + 40. Set your real maximum with `--max-luck N` or a
  `maxluck=N` line in `want.txt`.
- **Luck items:** at most 2 of each type, and only types your character can equip (Alucard can't
  wear Soul Eater Ring). Say how many you own with `--max-acc A/B` or a `maxacc=A/B` line.

```
# want.txt
Valmanway
maxluck=33
maxacc=1/0
```

How much this helps depends on the chest and your gear. For one boss chest, with LCK 33 and a
single Thief Ring, a Valmanway came up on about 1.1% of loads as-is and on about 3.5% of loads
once the gear changes were counted. With two type-A items and more Luck headroom it was close to
30%. A roll past the end of the item table picks a random pool item, and Luck doesn't change which
pool item it is, so those loads can't be helped.

### Item names

`item_names.json` lists item names for the base game and the DLC. If it's missing, the watcher
rebuilds it from your own game the first time it runs in a stage. It does this by decrypting the
game's text resources and reading the item databases from memory. After buying new DLC, run
`python3 gen_item_names.py` while in a stage to refresh it.

### Claude Code sidebar (optional)

`sidebar-mod/` is a Claude Code mod that shows the live prediction in a pane beside the chat. Keep
it inside this folder: it runs `../chest_watch.py` (or `chest_watch.py` in the folder Claude Code was
started in) with the first of `python3`, `py -3` or `python` that turns out to be Python 3. Load it
with:

```
claude --plugin-dir /path/to/hod-chest/sidebar-mod
```

| Command | What it does |
|---|---|
| `/chest` | Opens the pane and starts the watcher (restarts it if it stopped) |
| `/chest want Miser Ring, Valmanway` | Sets the targets in `want.txt`; `/chest want` alone clears them |
| `/chest maxluck 33` | Caps the Luck the suggestions may ask for; `/chest maxluck` resets it |
| `/chest maxacc 1/0` | How many type-A / type-B luck items you own; `/chest maxacc` resets it |

`TARGET HIT` and `TARGET REACHABLE` show in green and cyan, and each new one pops a toast and a
desktop notification. If the mod reloads, it restarts the watcher on its own.

## How the roll works

| Step | Detail |
|---|---|
| Chest table | Weighted `(category, index, weight)` entries, plus one or two "generic pool" ids |
| Filter | Each item's character bitmask has to include the opener's character |
| Weights | Equipment: `w = w + LCK`, then `w = (w + 20) * 12 / 10` once per luck accessory. Consumables: divided by 1, 2 or 4 |
| Roll | Pick a number from 0 to max(total, 10000). A roll past the table picks a random generic-pool item |
| RNG | Per-chest LCG `s = s * 0x6C078965 + 1`; output `(rotl(s, 16) & 0x7FFFFFFF) % range` |
| Stage base | The game's uptime in milliseconds when the stage loads, shared by everyone in the session |
| Seed | `stage_base + chest_id + 1 + connection_byte` (0 solo), then 11-30 warm-up steps |
| Luck lock | Luck and luck-item counts are read when you touch the chest, so the seed is fixed per load but the drop isn't |

## Known limits

- Luck is read using Alucard's stat layout. Other characters may read wrong until the game locks
  it in when you touch the chest.
- The Jonathan and Julius character ids (3 and 4) haven't been confirmed in game yet.
- Spell Glyph names come from a community list rather than the game files.
- `REACHABLE` doesn't know your inventory beyond what `maxluck`/`maxacc` tell it, and it can't
  tell how much Luck a luck item adds itself. Check the `LCK` the pane shows before you touch the
  chest.
- The Windows memory reader is untested. It assumes the emulator maps guest memory at host
  address `0x100000000`, as it does on Linux, and stops with a message if it can't find the game
  image there.

## Research tools

`ppcdis.py`, `xref.py` and `txrc.py` are reverse-engineering helpers. `ppcdis.py` and `xref.py`
need `xex_image.bin`, a dump of the game's code taken from your own running game (not included),
and `ppcdis.py` also needs `pip install capstone`. None of them are needed to use the predictor.
