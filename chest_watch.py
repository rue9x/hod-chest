"""Live boss-chest drop predictor for Castlevania: Harmony of Despair Recomp.

Reads guest RAM through xenia's /dev/shm backing file (no ptrace/root needed),
finds treasureBox objects and replays the game's own roll (guest fn 823AA6D8).

Usage: python3 chest_watch.py [--all] [--luck N] [--once] [--interval S]
"""
import argparse, json, os, struct, time

from gmem import Mem

TREASURE_VTABLE = 0x821E7700
RNG_REGISTRY = 0x8291DA08      # global -> std::map<int, Rng*> keyed by stream id
PLAYER_MGR = 0x8291D968        # +0x14: 6 player handles
HANDLE_TABLE = 0x8291D9F0      # +0xAC: handle -> object table (16-byte entries)
# Category base ids (guest .rdata 821D4878): category k covers [BASE[k], BASE[k+1])
BASE = [0x0, 0x1, 0x29, 0x33, 0x227, 0x60F, 0x673, 0x73B, 0x745, 0x80D,
        0x871, 0x8A3, 0x8AD, 0x911, 0x1C99, 0x2081, 0x208B]
CHEST_TYPES = {1: 'small', 2: 'gold', 3: 'type3', 4: 'BOSS', 5: 'type5', 6: 'type6'}
OPENED_STATES = {9}       # 2 = waiting for boss, 5 = armed/closed
NAMES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'item_names.json')


def f32(m, a):
    return struct.unpack('>f', m.read(a, 4))[0]


def category(item):
    for k in range(16):
        if item < BASE[k + 1]:
            return k
    return 0


def lcg_next(state):
    return (state * 0x6C078965 + 1) & 0xFFFFFFFF


def rand_range(state, lo, hi):
    """Guest fn 825C08C8: returns (value, new_state)."""
    state = lcg_next(state)
    r = ((state << 16 | state >> 16) & 0xFFFFFFFF) & 0x7FFFFFFF
    return (lo + r % (hi - lo) if hi > lo else lo), state


def rng_state(m, key):
    """Look up the Rng object for `key` in the registry map; return (obj, state)."""
    head = m.u32(m.u32(RNG_REGISTRY) + 4)
    node = m.u32(head + 4)
    while not m.u8(node + 0x15):
        k = m.s32(node + 0xC)
        if k == key:
            obj = m.u32(node + 0x10)
            return obj, m.u32(obj + 4)
        node = m.u32(node + 8) if k < key else m.u32(node)
    return None, None


def adjusted_weight(item, w, luck, acc_a, acc_b, div):
    """Weight transform from guest fn 823AA6D8."""
    cat = category(item)
    if cat in (1, 2, 13, 14):
        return max(w // div, 1)
    loops = acc_b if cat == 6 else acc_a
    w = luck + w
    for _ in range(loops):
        w = (w + 20) * 12 // 10
    return w


def local_player(m):
    arr = m.u32(m.u32(PLAYER_MGR) + 0x14)
    tbl = m.u32(m.u32(HANDLE_TABLE) + 0xAC)
    for i in range(6):
        h = m.u32(arr + 4 * i)
        if not h:
            continue
        e = tbl + ((h << 4) & 0xFFFF0)
        obj = m.u32(e + 4) if m.u32(e) == h else 0
        if obj and m.u32(obj + 0x930) == 1:
            return obj
    return None


def players(m):
    """[(slot, object, is_local)] for every player object in the stage."""
    out = []
    arr = m.u32(m.u32(PLAYER_MGR) + 0x14)
    tbl = m.u32(m.u32(HANDLE_TABLE) + 0xAC)
    for i in range(6):
        h = m.u32(arr + 4 * i)
        if not h:
            continue
        e = tbl + ((h << 4) & 0xFFFF0)
        obj = m.u32(e + 4) if m.u32(e) == h else 0
        if obj:
            out.append((i, obj, m.u32(obj + 0x930) == 1))
    return out


def character_id(m, obj):
    """Character id from the character DLL's vfunc 0x240, which is `li r3, N; blr`."""
    st = m.u32(obj + 0x924)
    f = m.u32(m.u32(st) + 0x240) if st else 0
    if not f:
        return None
    w0, w1 = m.u32(f), m.u32(f + 4)
    if w0 >> 16 == 0x3860 and w1 == 0x4E800020:     # li r3, N ; blr
        return w0 & 0xFFFF
    return None


def estimate_luck(m, p=None):
    """LCK = base + equip bonus. Offsets taken from dllAlucard's getters; other
    characters' DLLs may lay this out differently, so treat as an estimate."""
    p = p or local_player(m)
    if not p:
        return None
    s = m.u32(m.u32(p + 0x924) + 0x58)
    if not s:
        return None
    return f32(m, s + 0x5C) + f32(m, s + 0x70) + f32(m, s + 0xF8)


GURD_DB = 0x8291D984      # armor/accessory database global (base + DLC files)


def armor_record(m, idx):
    """Guest fn 8246D8E8 for GURD: record address of armor index `idx`, or None."""
    o = m.u32(GURD_DB)
    base = m.u32(m.u32(o + 4) + 0x10)
    if idx < m.u32(base + 8):
        return base + 0x14 + idx * 0x4E
    for e in range(m.u32(o + 0x10), m.u32(o + 0x14), 0x3C):
        h = m.u32(e + 0x2C)
        f = m.u32(h + 0x10) if h else 0
        if f and m.u32(f + 0xC) <= idx < m.u32(f + 0xC) + m.u32(f + 8):
            return f + 0x14 + (idx - m.u32(f + 0xC)) * 0x4E
    return None


def estimate_accessories(m, p=None):
    """Guest fns 823AAC68 / 823AAD18: equipped slots 2-7 with armor effect 0x0E / 0x0A, max 2 each."""
    p = p or local_player(m)
    eq = m.u32(p + 0x9C8) if p else 0
    if not eq:
        return 0, 0
    a = b = 0
    for slot in range(2, 8):
        item = m.u32(eq + 4 * slot)
        if item and category(item) == 4:
            rec = armor_record(m, item - BASE[4])
            eff = m.u16(rec + 0xE) if rec else 0
            a += eff == 0xE
            b += eff == 0xA
    return min(a, 2), min(b, 2)


def read_chest(m, h):
    b, e = m.u32(h + 0x10C), m.u32(h + 0x110)
    entries = []
    for p in range(b, e, 4):
        q = m.u32(p)
        entries.append((m.u32(q), m.u32(q + 4)) if q else (0, 0))
    return {
        'addr': h, 'key': m.s32(h + 4), 'state': m.s32(h + 0xE4), 'type': m.s32(h + 0xF0),
        'count': m.u32(h + 0x104), 'entries': entries,
        'snap': m.u8(h + 0x129), 'acc_a': m.u32(h + 0x12C), 'acc_b': m.u32(h + 0x130),
        'luck': m.u32(h + 0x134),
    }


_recipe_cache = {}


_luck_acc_cache = {}


def luck_accessories(m, cid):
    """(max type-A, max type-B) luck items character `cid` can wear: 2 each, or fewer if it can equip fewer kinds
    (each item counts once; two of the same item still count twice, so any equippable kind allows 2)."""
    if cid in _luck_acc_cache:
        return _luck_acc_cache[cid]
    import pool
    a = b = 0
    for i in range(BASE[5] - BASE[4]):
        try:
            rec = armor_record(m, i)
            eff = m.u16(rec + 0xE) if rec else 0
            if eff in (0xE, 0xA) and (not cid or pool.allowed(m, BASE[4] + i, cid)):
                a, b = (2, b) if eff == 0xE else (a, 2)
        except ValueError:
            continue
    _luck_acc_cache[cid] = (a, b)
    return a, b


def luck_recipes(c, rng, wants, names, max_a=2, max_b=2):
    """For this chest's fixed RNG state: [(item, acc_a, acc_b, lo, hi)] Luck ranges that drop a target."""
    key = (rng, c['count'], tuple(c['entries']), tuple(wants), max_a, max_b)
    if key in _recipe_cache:
        return _recipe_cache[key]
    out = []
    for a in range(max_a + 1):
        for b in range(max_b + 1):
            run = None                          # (item, lo) of the open Luck range
            for luck in range(302):
                item = predict(c, rng, luck, a, b)[0] if luck <= 300 else None
                hit = item if item is not None and wanted(item, wants, names) else None
                if run and run[0] != hit:
                    out.append((run[0], a, b, run[1], luck - 1))
                    run = None
                if hit and not run:
                    run = (hit, luck)
    if len(_recipe_cache) > 64:
        _recipe_cache.clear()
    _recipe_cache[key] = out
    return out


def fmt_recipes(recipes, names, luck, acc_a, acc_b):
    """Recipes nearest your current Luck/accessories first."""
    def cost(r):
        _, a, b, lo, hi = r
        dl = 0 if lo <= luck <= hi else min(abs(lo - luck), abs(hi - luck))
        return (abs(a - acc_a) + abs(b - acc_b)) * 50 + dl
    parts = []
    for it, a, b, lo, hi in sorted(recipes, key=cost)[:4]:
        lk = f'LCK {lo}' if lo == hi else f'LCK {lo}-{hi}'
        parts.append(f'{names.get(it, hex(it))}: {lk} with acc {a}/{b}')
    more = f' (+{len(recipes) - 4} more)' if len(recipes) > 4 else ''
    return '; '.join(parts) + more


def predict(c, rng, luck, acc_a, acc_b):
    n = c['count']
    div = 1 if acc_a + acc_b < 1 else 2 if acc_a + acc_b == 1 else 4
    weighted, total = [], 0
    for item, w in c['entries'][:n]:
        if item and w:
            w = adjusted_weight(item, w, luck, acc_a, acc_b, div)
            total += w
        weighted.append((item, w))
    roll_total = max(total, 10000)
    if n == 0:  # game skips the weighted roll entirely
        r = -1
    else:
        r, rng = rand_range(rng, 0, roll_total)
    cum = 0
    for item, w in weighted:
        if item and w:
            cum += w
            if cum > r:
                return item, 'table', r, roll_total, weighted
    extras = c['entries'][n:]
    if extras:
        j, _ = rand_range(rng, 0, len(extras))
        if extras[j][0]:
            return extras[j][0], f'pool[{j}/{len(extras)}]', r, roll_total, weighted
    if c['entries'] and c['entries'][0][0]:
        return c['entries'][0][0], 'fallback', r, roll_total, weighted
    return 7, 'fallback', r, roll_total, weighted


def load_names():
    try:
        with open(NAMES_FILE, encoding='utf-8') as fh:
            return {int(k, 0): v for k, v in json.load(fh).items()}
    except (OSError, ValueError):
        return {}


def ensure_names(m, names):
    """First run in a stage: build item_names.json from the player's own game (nothing shipped)."""
    if names:
        return names
    try:
        import gen_item_names
        built, _, _ = gen_item_names.build(m)
        if len(built) > 200:            # item databases are loaded (we're in a stage)
            gen_item_names.write(built)
            names.update(built)
    except (ValueError, OSError):
        pass
    return names


def fmt_item(item, names):
    nm = names.get(item)
    cat = category(item)
    return f'{cat:04X}:{item - BASE[cat]:04X} {nm or "?"}'


def scan_chests(m, lo, hi):
    pat = struct.pack('>I', TREASURE_VTABLE)
    return [a for a in m.search(pat, lo, hi) if a % 4 == 0]


SESSION = 0x829034E0 + 0x20     # online session object; members at +0x17C (slots 0x5F..)
LOCAL_MEMBER_VT = 0x82068DA8
SEED_BASE = 0x84                 # *(HANDLE_TABLE obj)+0x84: per-stage seed base, shared across players
FRIENDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'friends.txt')
WANT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'want.txt')


def want_list(args):
    """(target names lowercase, Luck cap or None, (max type-A, max type-B) luck items you own or None).
    --want/--max-luck/--max-acc, else want.txt: one item name per line, plus optional
    'maxluck=N' and 'maxacc=A/B' lines."""
    raw, cap, acc = list(args.want or []), args.max_luck, args.max_acc
    try:
        with open(WANT_FILE, encoding='utf-8-sig') as fh:
            lines = [ln.strip() for ln in fh if ln.strip() and not ln.startswith('#')]
    except OSError:
        lines = []
    for ln in lines:
        if ln.lower().startswith('maxacc='):
            if acc is None:
                try:
                    acc = parse_acc(ln.split('=', 1)[1])
                except ValueError:
                    pass
        elif ln.lower().startswith('maxluck='):
            if cap is None:
                try:
                    cap = int(ln.split('=', 1)[1])
                except ValueError:
                    pass
        elif not args.want:
            raw.append(ln)
    return [w.lower() for w in raw], cap, acc


def parse_acc(text):
    a, _, b = text.strip().partition('/')
    return max(0, min(2, int(a))), max(0, min(2, int(b or 0)))


def wanted(item, wants, names):
    nm = (names.get(item) or '').lower()
    return bool(nm) and any(w in nm for w in wants)


def session_bytes(m):
    """(local byte, [remote bytes]) used to offset chest seeds online (guest fn 82441B00)."""
    s = m.u32(SESSION)
    if not s:
        return None, []
    local, remote = None, []
    for i in range(8):
        mem = m.u32(s + (0x5F + i) * 4)
        if not mem:
            continue
        b = m.u8(mem + 0x18A)
        if m.u32(mem) == LOCAL_MEMBER_VT:
            local = b
        elif b not in remote:
            remote.append(b)
    return local, remote


def chest_state_from_seed(seed):
    """Chest init (guest 823A8540): seed the stream, draw n = rand(10,30), then n more steps."""
    n, st = rand_range(seed, 10, 30)
    for _ in range(n):
        st = lcg_next(st)
    return st


def friend_list(args):
    """[(character, seed byte)] from --friend 'Charlotte:0x21' or friends.txt lines 'Charlotte 0x21'.
    The byte is the friend's own connection id: (reuse counter << 5) | slot. Default guess 0x21."""
    raw = args.friend or []
    if not raw:
        try:
            with open(FRIENDS_FILE, encoding='utf-8-sig') as fh:
                raw = [ln.strip() for ln in fh if ln.strip() and not ln.startswith('#')]
        except OSError:
            pass
    out = []
    for item in raw:
        parts = item.replace(':', ' ').split()
        opts = dict(p.split('=', 1) for p in parts[1:] if '=' in p)
        nums = [p for p in parts[1:] if '=' not in p]
        out.append((parts[0], int(nums[0], 0) if nums else 0x21,
                    int(opts['luck']) if 'luck' in opts else None,
                    int(opts['acc']) if 'acc' in opts else None))
    return out


def chest_snapshot(m, h, c):
    """Everything needed to replay this chest for any player later (stored by the logger)."""
    rec, size = m.u32(h + 0x198), m.u32(h + 0x194)
    return {'key': c['key'], 'base': m.u32(m.u32(HANDLE_TABLE) + SEED_BASE),
            'pools': [x for x in (m.s32(rec + 8), m.s32(rec + 0xC)) if x >= 0],
            'table': [list(struct.unpack('>II', m.read(rec + o, 8))) for o in range(0x10, size, 8)]}


CHESTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chests.jsonl')


def save_snapshot(snap):
    """Append the chest to chests.jsonl once (dedup on key+base) for friend_byte.py."""
    try:
        with open(CHESTS_FILE, encoding='utf-8') as fh:
            last = fh.read().strip().rsplit('\n', 1)[-1]
        prev = json.loads(last) if last else None
        if prev and (prev['key'], prev['base']) == (snap['key'], snap['base']):
            return
    except (OSError, ValueError):
        pass
    with open(CHESTS_FILE, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(snap) + '\n')


def predict_other(m, snap, cid, byte, luck_steps=5, lucks=None, accs=(0, 1, 2)):
    """{item: [luck values]} for a player with character id `cid` and seed byte `byte`."""
    import pool
    st = chest_state_from_seed((snap['base'] + snap['key'] + 1 + byte) & 0xFFFFFFFF)
    count, entries = pool.build(m, [tuple(t) for t in snap['table']], snap['pools'], cid)
    res = {}
    for luck in (lucks if lucks is not None else range(0, 301, luck_steps)):
        for acc in accs:
            item = predict({'count': count, 'entries': list(entries)}, st, luck, acc, 0)[0]
            res.setdefault(item, []).append(luck)
    return res


def friend_lines(m, h, c, args, names, hits=None):
    """Predict each friend's drop: seed = shared stage base + key + 1 + their connection byte."""
    import pool
    friends = friend_list(args)
    local, remote = session_bytes(m)
    if local is None:
        return []
    if not friends:   # nobody configured: one default entry per remote member, character auto-detected
        friends = [('auto', 0x21 + k, None, None) for k in range(max(1, len(remote)))]
    snap = chest_snapshot(m, h, c)
    by_name = {v.lower(): k for k, v in pool.CHARS.items()}
    try:
        others = [obj for _, obj, local in players(m) if not local]
    except ValueError:
        others = []
    out = []
    for n, (cname, byte, luck, acc) in enumerate(friends):
        cid = by_name.get(cname.lower())
        obj = others[n] if n < len(others) else None
        read_luck = read_acc = None
        if obj:
            try:
                det = character_id(m, obj)
                if det in pool.CHARS:
                    cid, cname = det, pool.CHARS[det]
                lk = estimate_luck(m, obj)
                if lk is not None and 0 <= lk <= 999:
                    read_luck = int(lk)
                read_acc = estimate_accessories(m, obj)[0]
            except ValueError:
                pass
        if luck is None and read_luck is not None:
            luck = read_luck
        if acc is None and read_acc is not None:
            acc = read_acc
        if not cid:
            out.append(f'  friend #{n + 1}: character not detected yet' if cname == 'auto' else
                       f'  friend {cname}: unknown character (use one of {", ".join(pool.CHARS.values())})')
            continue
        res = predict_other(m, snap, cid, byte,
                            lucks=[max(0, min(300, luck))] if luck is not None else None,
                            accs=(acc,) if acc is not None else (0, 1, 2))
        tag = f'byte {byte:#04x}' + (f', LCK {luck}' if luck is not None else '') + (f', acc {acc}' if acc is not None else '')
        if hits is not None:
            for it in res:
                if wanted(it, args.wants, names):
                    hits.append((cname, it, len(res) == 1))
        if len(res) == 1:
            out.append(f'  friend {cname} [{tag}]: WILL DROP {fmt_item(next(iter(res)), names)}')
        else:
            parts = ', '.join(f'{names.get(it, hex(it))} (LCK {min(v)}-{max(v)})' for it, v in res.items())
            out.append(f'  friend {cname} [{tag}]: depends on their Luck/accessories: {parts}')
    return out


def report(m, chests, args, names, hits=None):
    lines = []
    est = None if args.luck is not None else estimate_luck(m)
    for h in chests:
        try:
            if m.u32(h) != TREASURE_VTABLE:
                continue
            c = read_chest(m, h)
        except ValueError:
            continue
        if c['state'] in OPENED_STATES or (not args.all and c['type'] != 4):
            continue
        obj, rng = rng_state(m, c['key'])
        if rng is None:
            lines.append(f'chest {h:08X}: RNG stream {c["key"]} not found')
            continue
        if c['snap']:
            luck, acc_a, acc_b, src = c['luck'], c['acc_a'], c['acc_b'], 'locked in by game'
        else:
            luck = args.luck if args.luck is not None else (est or 0)
            luck = max(0, min(300, int(luck)))
            acc_a, acc_b = (args.acc_a, args.acc_b) if args.acc_a is not None else estimate_accessories(m)
            src = 'override' if args.luck is not None else 'estimated from player'
        item, how, r, tot, weighted = predict(c, rng, luck, acc_a, acc_b)
        if hits is not None and c['type'] == 4 and wanted(item, args.wants, names):
            hits.append(('you', item, True))
        elif hits is not None and c['type'] == 4 and args.wants and not c['snap']:
            cap = args.luck_cap if args.luck_cap is not None else luck + 40
            try:
                me = local_player(m)
                max_a, max_b = luck_accessories(m, character_id(m, me) if me else None)
            except ValueError:
                max_a, max_b = 2, 2
            if args.acc_cap:
                max_a, max_b = min(max_a, args.acc_cap[0]), min(max_b, args.acc_cap[1])
            every = luck_recipes(c, rng, args.wants, names, max_a, max_b)
            recipes = [(it, a, b, lo, min(hi, cap)) for it, a, b, lo, hi in every if lo <= cap]
            if recipes:
                hits.append(('you', recipes[0][0], 'reach:' + fmt_recipes(recipes, names, luck, acc_a, acc_b)))
            elif every:
                hits.append(('you', every[0][0], f'far:only at LCK {min(r[3] for r in every)}+, above your cap of {cap}'))
        lines.append(f'{CHEST_TYPES.get(c["type"], c["type"])} chest @ {h:08X}  state {c["state"]}  '
                     f'rng[{c["key"]}]={rng:08X}  LCK {luck} acc {acc_a}/{acc_b} ({src})')
        lines.append(f'  >>> WILL DROP: {fmt_item(item, names)}  via {how}  (roll {r}/{tot})')
        tw = sum(w for _, w in weighted)
        for it, w in weighted:
            if it:
                lines.append(f'      {fmt_item(it, names):<40} w={w:<5} {100 * w / max(tw, 10000):5.1f}%')
        pool = [e[0] for e in c['entries'][c['count']:] if e[0]]
        if pool:
            lines.append(f'      + {len(pool)} pool items share {100 * max(0, 10000 - tw) / 10000:.1f}%: '
                         + ', '.join(names.get(i, f'{i:X}') for i in pool))
        if c['type'] == 4 and 2 <= c['state'] < 7:
            try:
                save_snapshot(chest_snapshot(m, h, c))
            except (ValueError, OSError):
                pass
        try:
            lines += friend_lines(m, h, c, args, names, hits if c['type'] == 4 else None)
        except ValueError:
            pass
    return lines


def enable_console():
    """Windows: UTF-8 output, and ANSI clear-screen support in classic consoles."""
    import sys
    if sys.platform != 'win32':
        return
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass
    if sys.stdout.isatty():
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)                       # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING


def main():
    enable_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--all', action='store_true', help='show every unopened chest, not just the boss chest')
    ap.add_argument('--luck', type=float, help='override LCK used before the game locks it in')
    ap.add_argument('--acc-a', type=int, help='override count of luck accessory A (0-2); default: read from equipment')
    ap.add_argument('--acc-b', type=int, default=0, help='override count of luck accessory B (with --acc-a)')
    ap.add_argument('--friend', action='append', help="friend as CHAR or CHAR:BYTE, repeatable; default: friends.txt")
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--interval', type=float, default=0.5)
    ap.add_argument('--rescan', type=float, default=5.0, help='seconds between object rescans')
    ap.add_argument('--lo', type=lambda s: int(s, 16), default=0x40000000, help='scan start (hex)')
    ap.add_argument('--hi', type=lambda s: int(s, 16), default=0x50000000, help='scan end (hex)')
    ap.add_argument('--want', action='append',
                    help='target item name (substring, any case), repeatable; default: want.txt')
    ap.add_argument('--max-acc', type=parse_acc,
                    help="most luck items you own, as A/B (type A: Thief Ring, Ebisu Raiment; type B: Soul Eater Ring); "
                         "default: maxacc= in want.txt, else 2/2 limited by what your character can equip")
    ap.add_argument('--max-luck', type=int,
                    help='highest LCK you can reach with gear swaps (default: maxluck= in want.txt, else current + 40)')
    args = ap.parse_args()

    names = load_names()
    m, chests, last_scan, last_out = Mem(), [], 0.0, None
    bases, rang = set(), None    # stage seeds seen this run (= reroll count); base the bell last rang on
    while True:
        now = time.time()
        if now - last_scan > args.rescan:
            chests, last_scan = scan_chests(m, args.lo, args.hi), now
        ensure_names(m, names)
        args.wants, args.luck_cap, args.acc_cap = want_list(args)
        hits = []
        out = report(m, chests, args, names, hits) or ['no unopened boss chest in this stage yet']
        if args.wants and out[0].startswith('BOSS'):
            try:
                base = m.u32(m.u32(HANDLE_TABLE) + SEED_BASE)
            except ValueError:
                base = None
            bases.add(base)
            sure = [h for h in hits if h[2] is True]
            reach = [h[2][6:] for h in hits if isinstance(h[2], str) and h[2].startswith('reach:')]
            far = [h[2][4:] for h in hits if isinstance(h[2], str) and h[2].startswith('far:')]
            hits = [h for h in hits if h[2] is False]
            if sure:
                who = ', '.join(f'{"you get" if nm == "you" else nm + " gets"} {names.get(it, hex(it))}' for nm, it, _ in sure)
                head = f'*** TARGET HIT (load #{len(bases)}): {who}. Open the chest!'
                if not args.once and rang != base:
                    head += '\a'
                    rang = base
            elif reach:
                head = (f'+++ TARGET REACHABLE (load #{len(bases)}): change gear BEFORE touching the chest. '
                        + reach[0])
            elif hits:
                who = ', '.join(f'{nm} might get {names.get(it, hex(it))}' for nm, it, _ in hits)
                head = f'??? TARGET POSSIBLE (load #{len(bases)}): {who}, depending on their Luck'
            else:
                head = f'--- no target this load (#{len(bases)}); reload. Want: {", ".join(args.wants)}'
                if far:
                    head += f' ({far[0]})'
            unknown = [w for w in args.wants if not any(w in v.lower() for v in names.values())]
            if unknown and names:
                head += f'\n    (no item name contains: {", ".join(unknown)})'
            out = [head] + out
        if args.once:
            print('\n'.join(out).replace('\a', ''))
            return
        if out != last_out:
            print('\033[2J\033[H' + time.strftime('%H:%M:%S') + '\n' + '\n'.join(out), flush=True)
            last_out = [o.replace('\a', '') for o in out]
        time.sleep(args.interval)


if __name__ == '__main__':
    main()
