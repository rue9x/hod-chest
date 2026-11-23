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

On Windows, use `py` or `python` wherever this README says `python3`.

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

### Item names

`item_names.json` lists item names for the base game and the DLC. If it's missing, the watcher
rebuilds it from your own game the first time it runs in a stage. It does this by decrypting the
game's text resources and reading the item databases from memory. After buying new DLC, run
`python3 gen_item_names.py` while in a stage to refresh it.

### Claude Code sidebar (optional)

`sidebar-mod/` is a Claude Code mod that shows the prediction in a pane beside the chat. Keep it
inside this folder, because it runs `../chest_watch.py` with the first of `python3`, `py -3` or
`python` that turns out to be Python 3. Load it with:

```
claude --plugin-dir /path/to/hod-chest-predictor/sidebar-mod
```

Then run `/chest` to open the pane.

## How the roll works

| Step | Detail |
|---|---|
| Chest table | Weighted `(category, index, weight)` entries, plus one or two "generic pool" ids |
| Filter | Each item's character bitmask has to include the opener's character |
| Weights | Equipment: `w = w + LCK`, then `w = (w + 20) * 12 / 10` once per luck accessory. Consumables: divided by 1, 2 or 4 |
| Roll | Pick a number from 0 to max(total, 10000). A roll past the table picks a random generic-pool item |
| RNG | Per-chest LCG `s = s * 0x6C078965 + 1`; output `(rotl(s, 16) & 0x7FFFFFFF) % range` |
| Seed (online) | `stage_base + chest_id + 1 + connection_byte`, then 11-30 warm-up steps |

## Known limits

- Luck is read using Alucard's stat layout. Other characters may read wrong until the game locks
  it in when you touch the chest.
- The Jonathan and Julius character ids (3 and 4) haven't been confirmed in game yet.
- Spell Glyph names come from a community list rather than the game files.
- The Windows memory reader is untested. It assumes the emulator maps guest memory at host
  address `0x100000000`, as it does on Linux, and stops with a message if it can't find the game
  image there.
