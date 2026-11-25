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

## Windows app

Download `hod-chest.exe` from the repository's **Releases** page and double-click it. It starts the
watcher and opens the live table at http://localhost:8765/ in your browser; close its window (or
press **Stop watcher** on the page) to quit. No Python needed. It keeps its files (item names, the
chest log, saved cheat positions) in the folder it's in. Any `chest_watch.py` option works after
it, e.g. `hod-chest.exe --want "you: Valmanway +1"`.

The Windows build is new and hasn't been tried against the game on Windows yet. The cheats work on
both systems (on Windows through `WriteProcessMemory`), but on Windows they're untested with the
game too.

To build it yourself: `./build_exe.sh` on Linux (uses Wine; it fetches Windows Python from
python.org on first run), or `pip install pyinstaller` then `pyinstaller hod-chest.spec` on
Windows. Either way the result is `dist/hod-chest.exe`.

## Quick start

It's a standalone Python app. Claude Code is not needed; the sidebar mod below is optional.

```
python3 chest_watch.py --serve 8765
```

Then open http://localhost:8765/ in a browser. That's the full app: live status, a row per player,
desired-drop dropdowns, settings, drop lists and a Stop button. Settings can also be given at
start-up:

```
python3 chest_watch.py --serve 8765 --want "you: Valmanway +1" --max-luck 33 --max-acc 1/0
```

On Windows, use `py chest_watch.py --serve 8765`.

## Use

```
python3 chest_watch.py           # text view in the terminal; refreshes when anything changes
python3 chest_watch.py --once    # one snapshot
python3 chest_watch.py --all     # every unopened chest, not just the boss chest
python3 chest_watch.py --json    # one JSON record per refresh: status plus a row per player
python3 chest_watch.py --serve 8765   # also a live HTML table at http://localhost:8765/
python3 players.py               # each player's detected character, Luck and luck accessories
```

The boss chest shows up as soon as the stage loads. The prediction becomes final when you touch
the chest, which is when the game locks in your Luck. Equipment changes before then can change it.

### Settings reset every run

Nothing you set is saved. Desired drops, Luck caps, luck items owned and friends' details all
live in the watcher's memory and reset when it restarts. Set them on the web page (below), with
the sidebar's `/chest` commands, or with command-line flags at start-up.

### Live web table (`--serve`)

```
python3 chest_watch.py --serve 8765
```

Then open http://localhost:8765/. The page is served to your own computer only (`127.0.0.1`), and
refreshes every second. It shows:

- **Live** or **Not live**, with the reason: the watcher stopped, the game isn't running, or the
  game is still starting
- when the prediction last changed, and when the watcher last checked
- one row per player: **Player** (gamertag), **Character**, **Luck**, **Desired drop**, **Can
  manipulate** and **Expected drop**
- under the table, each player's settings and their drop list with the odds at their current Luck

Each row's **Desired drop** is a dropdown of that player's possible drops; `None` clears it. Each
player's settings are:

| Setting | What it does |
|---|---|
| Luck cap | The most Luck the gear suggestions may ask of them. Default: their Luck + 40 |
| Luck items owned | How many type-A (Thief Ring, Ebisu Raiment) and type-B (Soul Eater Ring) items they have. Default: whatever their character can equip |
| Luck (friends) | Overrides the Luck read from the game |
| Thief Rings / Ebisu worn, Soul Eater Rings worn (friends) | Override the luck items read from the game (0-2 each; `--friend ... acc=A/B` does the same) |
| Owns every luck item, Luck with no luck gear (friends) | Suggest whole outfits instead of Luck ranges (see below) |

**Stop watcher**, at the top of the page, ends the watcher and the page with it (settings reset).
`/chest` or running `chest_watch.py` again starts it back up.

Only the page itself can change settings or stop the watcher; requests from other websites are refused. The page is
`web/index.html`, `web/style.css` and `web/app.js`.

### Co-op

Each player rolls their own chest. The seed is the stage's shared seed plus that player's
connection byte. Online, the watcher finds everyone on its own: each player's gamertag, seat
(connection byte), character and Luck, so there's nothing to set up.

If the game's member list can't be read, name friends by hand with `--friend Character:0x21`
(repeatable). If a friend's real drop doesn't match the prediction, record it and the tool narrows
down the right byte:

```
python3 friend_byte.py add Richter "Megingiord"              # their drop from the latest boss chest
python3 friend_byte.py add Richter "Megingiord" SomeFriend   # say who, if two friends play Richter
```

A friend's byte only holds for one session, so drops are grouped by character and friend.

Boss chests are saved to `chests.jsonl` automatically for this.

### Rerolling for a target

The stage seed is the game's uptime in milliseconds at the moment the stage loads. A one-millisecond
difference gives a completely different drop, so you can't time the "Start the Game" press. What you
*can* do is reload and let the watcher tell you which loads are worth opening.

Pick each player's desired drop on the page, or name targets with `--want` (repeatable) or
`/chest want`. Names match as case-insensitive substrings, ignoring spaces and apostrophes, so
`valmanway` also matches `Valmanway +1` and `Dracula's Soul` matches `Dracula Soul`.

A typed target with no prefix counts for anyone. To aim one at a single player, put who it's for
in front:

| Prefix | Who |
|---|---|
| `you:` or your character's name | You |
| a character name, like `Richter:` | The friend playing that character |
| a gamertag, like `SomeFriend:` | That player (the Player column) |
| `all:` or none | Anyone |

```
python3 chest_watch.py --want "you: Miser Ring" --want "Richter: Megingiord" --max-luck 33 --max-acc 1/0
```

**Can manipulate** then says what to do:

| Can manipulate | Meaning |
|---|---|
| `YES: open it now` | Their drop is a target. Open the chest. |
| `yes: Valmanway: LCK 21-33 with acc 0/0` | Not as they are, but this load gives a target with other gear. Change to that setup **before** touching the chest, and wait for `YES`. |
| `maybe (Luck unknown)` | They might get a target, but their Luck isn't known. |
| `no` | Nothing they can wear helps this load. Reload. |
| `-` | No desired drop. |

The terminal view says the same in its top line (`*** TARGET HIT`, `+++ TARGET REACHABLE`,
`??? TARGET POSSIBLE`, `--- no target`), with a count of stage loads seen, and rings the terminal
bell once per hit.

A `yes:` setup is a Luck range plus how many type-A / type-B luck items to wear. Setups closest to
the player's current gear come first, and only ones they can build are shown: Luck up to their
Luck cap, and no more luck items than they own or their character can equip (Alucard can't wear
Soul Eater Ring).

If a friend owns every luck item, tick **Owns every luck item** and enter their Luck with no luck
gear on. The watcher then tries every outfit of luck gear their character can equip and names the
smallest one that works, such as `Retro Helm: wear Ebisu Raiment, Thief Ring, nothing else with
Luck (LCK 19, acc 2/0)`. Weapon Luck isn't counted.

How much this helps depends on the chest and your gear. For one boss chest, with LCK 33 and a
single Thief Ring, a Valmanway came up on about 1.1% of loads as-is and on about 3.5% of loads
once the gear changes were counted. With two type-A items and more Luck headroom it was close to
30%. A roll past the end of the item table picks a random pool item, and Luck doesn't change which
pool item it is, so those loads can't be helped.

### Item names

`item_names.json` lists item names for the base game and the DLC you own. It isn't included: the
watcher builds it from your own game the first time it runs in a stage. It does this by decrypting the
game's text resources and reading the item databases from memory. After buying new DLC, run
`python3 gen_item_names.py` while in a stage to refresh it.

### Claude Code sidebar (optional)

You don't need this to use the predictor: it only adds a pane beside the chat and `/chest`
commands, and runs the same `chest_watch.py --serve 8765` for you. Everything `/chest` does can be
done on the web page.

`sidebar-mod/` is a Claude Code mod that shows the live prediction in a pane beside the chat. Keep
it inside this folder: it runs `../chest_watch.py` (or `chest_watch.py` in the folder Claude Code was
started in) with the first of `python3`, `py -3` or `python` that turns out to be Python 3. Load it
with:

```
claude --plugin-dir /path/to/hod-chest/sidebar-mod
```

| Command | What it does |
|---|---|
| `/chest` | Opens the pane and starts the watcher, with the web table on http://localhost:8765/ (restarts it if it stopped) |
| `/chest want Miser Ring, SomeFriend: Megingiord` | Replaces the desired drops (prefixes as above); `wants`, `wanted`, `desire` and `desired` work too. `/chest want` alone clears them (None) |
| `/chest maxluck 33` | Caps the Luck the suggestions may ask of you; `/chest maxluck` resets it |
| `/chest maxacc 1/0` | How many type-A / type-B luck items you own; `/chest maxacc` resets it |

The pane shows:

- **Live** or **Not live**, with the reason: the watcher isn't running, the game isn't running, or
  the game is still starting
- when the prediction last changed, and when the watcher last checked
- a table with one row per player: **Player** (gamertag), **Character**, **Luck**, **Desired drop**
  (`None` without a target), **Can manipulate** (`YES: open it now`, `yes:` plus the gear setup, `maybe`, or
  `no`) and **Expected drop**
- each player's drop list with the odds at their current Luck

Settings changed in the pane's commands and on the web page are the same settings, so the two
always agree. Each new `TARGET HIT` or `TARGET REACHABLE` pops a toast and a desktop notification.
If the mod reloads, it restarts the watcher on its own; the settings reset with it. The pane runs
`chest_watch.py --json --serve 8765`; `--json` prints one JSON record per refresh, if you want to
build your own view.

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
- The Jonathan and Julius character ids (3 and 4) haven't been confirmed in game yet. Simon (10) and
  Getsu Fuma (11) were identified from their item lists; Getsu Fuma's was confirmed in game.
- Spell Glyph names come from a community list rather than the game files.
- `REACHABLE` doesn't know your inventory beyond what `maxluck`/`maxacc` tell it, and it can't
  tell how much Luck a luck item adds itself. Check the `LCK` the pane shows before you touch the
  chest.
- The Windows memory reader is untested. It assumes the emulator maps guest memory at host
  address `0x100000000`, as it does on Linux, and stops with a message if it can't find the game
  image there.

## Cheats

`cheats/` holds tools that **write** to the game, such as saving your position and moving back
to it. They're separate from the predictor, which never writes, and the web page's **Cheats**
section runs them for you (pick one, press **Activate**). See [cheats/README.md](cheats/README.md).

## Research tools

`ppcdis.py` and `xref.py` are reverse-engineering helpers. They need `xex_image.bin`, a dump of
the game's code taken from your own running game (not included), and `ppcdis.py` also needs
`pip install capstone`. Neither is needed to use the predictor. `txrc.py` is: it decodes the
game's text, which is where item names come from.
