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

# Every setting lives in memory only and resets when the watcher restarts. The web page (and the
# sidebar's /chest command, through the same page server) changes it with POST /set.
#   wants:   [(who or None, item name)]; who is a lowercase player label, None means anyone
#   players: {player key, lowercase ('you' or a gamertag): {maxluck, maxacc, owns, base, luck}}
SETTINGS = {'wants': [], 'players': {}}
PLAYER_FIELDS = ('maxluck', 'maxacc', 'owns', 'base', 'luck')


def want_list(args):
    """(targets [(who or None, name)], your Luck cap or None, your (type-A, type-B) luck items or None)."""
    mine = SETTINGS['players'].get('you', {})
    try:
        cap = int(mine['maxluck']) if 'maxluck' in mine else None
        acc = parse_acc(mine['maxacc']) if 'maxacc' in mine else None
    except ValueError:
        cap = acc = None
    return list(SETTINGS['wants']), cap, acc


ANYONE = ('all', 'anyone', 'everyone', 'any')


def set_player_want(key, labels, item, everyone):
    """Make `item` (or nothing, if empty) the one target of the player known by `labels`. Targets for
    anyone are kept for everyone else, as one copy per other player in `everyone` [(key, labels)]."""
    labels = {l.lower() for l in labels} | {key.lower()}
    keep, shared = [], []
    for who, name in SETTINGS['wants']:
        if who is None:
            shared.append(name)
        elif who not in labels:
            keep.append((who, name))
    for k, ls in everyone:
        if not ({l.lower() for l in ls} | {k.lower()}) & labels:
            keep += [(k.lower(), name) for name in shared]
    if item:
        keep.append((key.lower(), item))
    SETTINGS['wants'] = keep


def apply_setting(req, rows):
    """One change from POST /set: {field, value, key?}. Returns what it did, or raises ValueError."""
    field, value = str(req.get('field', '')), str(req.get('value') or '').strip()
    if field == 'wants':        # the whole list: 'Item, Gamertag: Item'
        SETTINGS['wants'] = [split_who(w) for w in value.split(',') if w.strip()]
        return f'Desired drop: {fmt_wants(SETTINGS["wants"])}' if SETTINGS['wants'] else 'Desired drop cleared (None).'
    key = str(req.get('key') or 'you')
    if field == 'want':         # one player's only target
        row = next((r for r in rows if r.get('key', '').lower() == key.lower()), None)
        if row is None and key.lower() == 'you':
            row = {'key': 'you', 'labels': ['you', 'me'], 'player': 'You'}
        if row is None:
            raise ValueError(f'no player {key!r} in this stage')
        set_player_want(row['key'], row.get('labels', []), value, [(r['key'], r.get('labels', [])) for r in rows])
        return f'{row["player"]}: desired drop {value or "None"}'
    if field not in PLAYER_FIELDS:
        raise ValueError(f'unknown setting {field!r}')
    if value:
        if field == 'maxacc':
            a, b = parse_acc(value)
            value = f'{a}/{b}'
        elif field == 'owns':
            value = 'all'
        elif not value.isdigit() or int(value) > 999:
            raise ValueError(f'{field} must be a whole number from 0 to 999')
    opts = dict(SETTINGS['players'].get(key.lower(), {}))
    if value:
        opts[field] = value
    else:
        opts.pop(field, None)
    SETTINGS['players'] = {**SETTINGS['players'], key.lower(): opts}
    return f'{key}: {field} {value or "reset"}'


def split_who(text):
    """'Richter: Megingiord' -> ('richter', 'Megingiord'); 'Megingiord' -> (None, 'Megingiord')."""
    who, sep, name = text.partition(':')
    who = who.strip().lower()
    if sep and name.strip() and who:
        return (None if who in ANYONE else who), name.strip()
    return None, text.strip()


def wants_for(wants, labels):
    """Target names that apply to a player known by any of `labels`."""
    return [nm for who, nm in wants if who is None or who in labels]


def fmt_wants(wants):
    return ', '.join(f'{who}: {nm}' if who else nm for who, nm in wants)


def parse_acc(text):
    a, _, b = text.strip().partition('/')
    return max(0, min(2, int(a))), max(0, min(2, int(b or 0)))


def squash(text):
    """Lowercase with spaces and apostrophes dropped: 'Valmanway+1' matches 'Valmanway +1',
    "Dracula's Soul" matches 'Dracula Soul'."""
    return ''.join(text.lower().replace('\u2019', "'").replace("'s", '').replace("'", '').split())


def wanted(item, wants, names):
    nm = squash(names.get(item) or '')
    return bool(nm) and any(squash(w) in nm for w in wants)


MEMBER_SLOTS, MEMBER_NAME = 0x40, 0x158   # session +0x100: members in slot order; UTF-16 gamertag at +0x158


def session_members(m):
    """{connection byte: gamertag} for everyone in the online session, {} offline."""
    out = {}
    try:
        s = m.u32(SESSION)
        if not s:
            return out
        for i in range(8):
            mem = m.u32(s + (MEMBER_SLOTS + i) * 4)
            if not mem or m.u32(mem) != LOCAL_MEMBER_VT:
                continue
            raw, tag = m.read(mem + MEMBER_NAME, 32), ''
            for k in range(0, 32, 2):
                ch = int.from_bytes(raw[k:k + 2], 'big')
                if not ch:
                    break
                tag += chr(ch)
            if tag.isprintable() and tag:
                out[m.u8(mem + 0x18A)] = tag
    except ValueError:
        pass
    return out


def seats(m):
    """(local byte or None, [(byte, gamertag, player object or None)] for everyone else), matching the
    session's members to the stage's player objects by seat order (byte & 0x1F is the seat)."""
    members = session_members(m)
    try:
        plist = sorted(players(m))
    except ValueError:
        plist = []
    order = sorted(members, key=lambda b: b & 0x1F)
    local, objs = None, {}
    if len(plist) == len(order):
        for b, (_, obj, loc) in zip(order, plist):
            if loc:
                local = b
            else:
                objs[b] = obj
    else:   # someone still loading: fall back to seat number = player slot
        for i, obj, loc in plist:
            b = next((b for b in order if b & 0x1F == i), None)
            if b is None:
                continue
            if loc:
                local = b
            else:
                objs[b] = obj
    return local, [(b, members[b], objs.get(b)) for b in order if b != local]


def friend_overrides():
    """{gamertag, lowercase: options} set on the page for friends."""
    return {k: v for k, v in SETTINGS['players'].items() if k != 'you'}


def friend_bytes(args):
    """The connection byte each --friend entry resolves to, in order."""
    return [b if b is not None else 0x21 + n for n, (_, b, *_rest) in enumerate(friend_list(args))]


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
    """[(character, seed byte, ...)] from --friend 'Charlotte:0x21', for when the game's member list
    can't be read. The byte is the friend's connection id: (reuse counter << 5) | slot."""
    raw = args.friend or []
    out = []
    for item in raw:
        parts = item.replace(':', ' ').split()
        opts = dict(p.split('=', 1) for p in parts[1:] if '=' in p)
        nums = [p for p in parts[1:] if '=' not in p]
        out.append((parts[0], int(nums[0], 0) if nums else None,
                    int(opts['luck']) if 'luck' in opts else None,
                    int(opts['acc']) if 'acc' in opts else None,
                    {k: opts[k] for k in ('maxluck', 'maxacc', 'owns', 'base') if k in opts}))
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


ARMOR_SLOT, ARMOR_LUCK = 0xC, 0x36     # GURD record: 1 helm, 2 body, 3 cloak, 4 boots, 5 accessory; LCK bonus
SLOT_NAMES = {1: 'head', 2: 'body', 3: 'cloak', 4: 'feet', 5: 'accessory'}
_outfit_cache = {}


def outfits(m, cid, names):
    """{(gear LCK, type-A, type-B): fewest luck items giving it} over every outfit of luck gear `cid`
    can equip, one of each item, two accessory slots. Slots left out hold anything without luck."""
    if cid in _outfit_cache:
        return _outfit_cache[cid]
    import itertools, pool
    by_slot = {k: [] for k in SLOT_NAMES}
    for i in range(BASE[5] - BASE[4]):
        try:
            rec = armor_record(m, i)
            if not rec:
                continue
            lck, eff, slot = m.s16(rec + ARMOR_LUCK), m.u16(rec + 0xE), m.u16(rec + ARMOR_SLOT)
            if slot in by_slot and (lck > 0 or eff in (0xE, 0xA)) and pool.allowed(m, BASE[4] + i, cid):
                by_slot[slot].append((names.get(BASE[4] + i, hex(BASE[4] + i)), lck, eff == 0xE, eff == 0xA))
        except ValueError:
            continue
    none = [None]
    pick = lambda slot: none + by_slot[slot]
    accs = [()] + [(x,) for x in by_slot[5]] + list(itertools.combinations(by_slot[5], 2))
    best = {}
    for gear in itertools.product(pick(1), pick(2), pick(3), pick(4), accs):
        items = [g for g in gear[:4] if g] + list(gear[4])
        key = (sum(g[1] for g in items), min(2, sum(g[2] for g in items)), min(2, sum(g[3] for g in items)))
        if key not in best or len(items) < len(best[key]):
            best[key] = [g[0] for g in items]
    _outfit_cache[cid] = best
    return best


def outfit_reach(m, st, fc, cid, cname, base, luck, wants, names):
    """Hit tuples for a player who owns every luck item: the smallest outfit that drops a target."""
    found = []
    for (gl, a, b), items in outfits(m, cid, names).items():
        total = max(0, min(300, base + gl))
        item = predict(fc, st, total, a, b)[0]
        if wanted(item, wants, names):
            found.append((len(items), abs(total - luck), item, total, a, b, items))
    if not found:
        return []
    found.sort(key=lambda f: f[:2])
    _, _, item, total, a, b, items = found[0]
    wear = ', '.join(items) if items else 'no luck gear at all'
    return [(cname, item, f'reach:{names.get(item, hex(item))}: wear {wear}, nothing else with Luck '
                          f'(LCK {total}, acc {a}/{b})')]


def drop_odds(c, weighted, names):
    """[[item name, % chance]] for one player's chest: weighted items, then the pool share."""
    tw = sum(w for _, w in weighted)
    out = [[names.get(it, hex(it)), round(100 * w / max(tw, 10000), 1)] for it, w in weighted if it]
    pool_items = [e[0] for e in c['entries'][c['count']:] if e[0]]
    if pool_items:
        each = 100 * max(0, 10000 - tw) / 10000 / len(pool_items)
        out += [[names.get(i, hex(i)), round(each, 1)] for i in pool_items]
    return sorted(out, key=lambda r: -r[1])


def manip_cell(new, wants):
    """The table's "can manipulate" cell from the hit tuples one player added."""
    if not wants:
        return ''
    flags = [h[2] for h in new]
    if any(f is True for f in flags):
        return 'YES: open it now'
    reach = [f[6:] for f in flags if isinstance(f, str) and f.startswith('reach:')]
    if reach:
        return 'yes: ' + reach[0].split('; ')[0]
    if any(f is False for f in flags):
        return 'maybe (Luck unknown)'
    far = [f[4:] for f in flags if isinstance(f, str) and f.startswith('far:')]
    return 'no: ' + far[0] if far else 'no'


def friend_reach(m, snap, cid, byte, cname, luck, acc, limits, wants, names):
    """Gear suggestions for a friend whose Luck is known, as hit tuples like the local player's."""
    import pool
    st = chest_state_from_seed((snap['base'] + snap['key'] + 1 + byte) & 0xFFFFFFFF)
    count, entries = pool.build(m, [tuple(t) for t in snap['table']], snap['pools'], cid)
    fc = {'count': count, 'entries': list(entries)}
    if limits.get('owns') == 'all' and 'base' in limits:
        try:
            return outfit_reach(m, st, fc, cid, cname, int(limits['base']), luck, wants, names)
        except ValueError:
            pass
    max_a, max_b = luck_accessories(m, cid)
    try:
        if 'maxacc' in limits:
            la, lb = parse_acc(limits['maxacc'])
            max_a, max_b = min(max_a, la), min(max_b, lb)
        cap = int(limits['maxluck']) if 'maxluck' in limits else luck + 40
    except ValueError:
        cap = luck + 40
    every = luck_recipes(fc, st, wants, names, max_a, max_b)
    recipes = [(it, a, b, lo, min(hi, cap)) for it, a, b, lo, hi in every if lo <= cap]
    if recipes:
        return [(cname, recipes[0][0], 'reach:' + fmt_recipes(recipes, names, luck, acc, 0))]
    if every:
        return [(cname, every[0][0], f'far:{cname} only at LCK {min(r[3] for r in every)}+, above their cap of {cap}')]
    return []


def friend_lines(m, h, c, args, names, hits=None):
    """Predict each friend's drop: seed = shared stage base + key + 1 + their connection byte."""
    import pool
    friends = friend_list(args)
    members = session_members(m)
    _, seated = seats(m)
    seat_obj = {b: obj for b, _, obj in seated}
    if seated:   # online: everyone in the session, in seat order; the page's settings add details
        given = {f[1]: f for f in friends if f[1] is not None}
        over = friend_overrides()
        friends = []
        for b, gt, _ in seated:
            name, _, luck, acc, limits = given.get(b, ('auto', b, None, None, {}))
            o = over.get(gt.lower(), {})
            try:
                luck = int(o['luck']) if 'luck' in o else luck
                acc = int(o['acc']) if 'acc' in o else acc
            except ValueError:
                pass
            friends.append((o.get('char', name), b, luck, acc,
                            {**limits, **{k: o[k] for k in ('maxluck', 'maxacc', 'owns', 'base') if k in o}}))
    else:
        local, remote = session_bytes(m)
        if local is None:
            return []
        if not friends:   # nobody configured: one default entry per remote member, character auto-detected
            friends = [('auto', None, None, None, {}) for _ in range(max(1, len(remote)))]
    snap = chest_snapshot(m, h, c)
    by_name = {v.lower(): k for k, v in pool.CHARS.items()}
    try:
        others = [obj for _, obj, local in players(m) if not local]
    except ValueError:
        others = []
    out = []
    for n, (cname, byte, luck, acc, limits) in enumerate(friends):
        if byte is None:   # no byte on the line: guess (host is 0x20, guests 0x21, 0x22.. in join order)
            byte = 0x21 + n
        cid = by_name.get(cname.lower())
        obj = seat_obj.get(byte) if seated else (others[n] if n < len(others) else None)
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
        rows = getattr(args, 'rows', None) if hits is not None else None
        if not cid:
            if rows is not None:
                rows.append({'key': members.get(byte) or f'friend{n + 1}',
                             'labels': [(members.get(byte) or f'friend{n + 1}').lower()],
                             'settings': SETTINGS['players'].get((members.get(byte) or f'friend{n + 1}').lower(), {}),
                             'player': members.get(byte) or f'friend {n + 1}', 'char': '?', 'luck': '?',
                             'want': 'None', 'manip': '-', 'drop': 'character not detected yet', 'odds': []})
            out.append(f'  friend #{n + 1}: character not detected yet' if cname == 'auto' else
                       f'  friend {cname}: unknown character (use one of {", ".join(pool.CHARS.values())})')
            continue
        res = predict_other(m, snap, cid, byte,
                            lucks=[max(0, min(300, luck))] if luck is not None else None,
                            accs=(acc,) if acc is not None else (0, 1, 2))
        tag = f'byte {byte:#04x}' + (f', LCK {luck}' if luck is not None else '') + (f', acc {acc}' if acc is not None else '')
        gt = members.get(byte)
        label = f'{cname} ({gt})' if gt else cname
        flabels = {cname.lower(), cname.lower().split()[-1], f'friend{n + 1}'} | ({gt.lower()} if gt else set())
        mine = wants_for(args.wants, flabels) if hits is not None else []
        h0 = len(hits) if hits is not None else 0
        if mine:
            for it in res:
                if wanted(it, mine, names):
                    hits.append((label, it, len(res) == 1))
            if len(res) == 1 and not wanted(next(iter(res)), mine, names) and luck is not None:
                hits += friend_reach(m, snap, cid, byte, label, luck, acc or 0, limits, mine, names)
        if rows is not None:
            drop = (names.get(next(iter(res)), '?') if len(res) == 1 else
                    'by Luck: ' + ', '.join(f'{names.get(it, hex(it))} ({min(v)}-{max(v)})' for it, v in res.items()))
            count, entries = pool.build(m, [tuple(t) for t in snap['table']], snap['pools'], cid)
            fc = {'count': count, 'entries': list(entries)}
            st = chest_state_from_seed((snap['base'] + snap['key'] + 1 + byte) & 0xFFFFFFFF)
            weighted = predict(fc, st, max(0, min(300, luck or 0)), acc or 0, 0)[4]
            rows.append({'key': gt or f'friend{n + 1}', 'labels': sorted(flabels),
                         'settings': SETTINGS['players'].get((gt or f'friend{n + 1}').lower(), {}),
                         'player': gt or f'friend {n + 1}', 'char': cname,
                         'luck': str(luck) if luck is not None else '?', 'want': ', '.join(mine) or 'None',
                         'manip': manip_cell(hits[h0:], mine) or '-', 'drop': drop,
                         'odds': drop_odds(fc, weighted, names)})
        if len(res) == 1:
            out.append(f'  friend {label} [{tag}]: WILL DROP {fmt_item(next(iter(res)), names)}')
        else:
            parts = ', '.join(f'{names.get(it, hex(it))} (LCK {min(v)}-{max(v)})' for it, v in res.items())
            out.append(f'  friend {label} [{tag}]: depends on their Luck/accessories: {parts}')
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
        mine, labels, who, me_char, me_gt = [], {'you', 'me'}, '', '', ''
        h0 = len(hits) if hits is not None else 0
        if c['type'] == 4:
            try:
                me = local_player(m)
                cid = character_id(m, me) if me else None
                if cid:
                    import pool
                    labels |= {pool.CHARS.get(cid, '').lower(), pool.CHARS.get(cid, ' ').lower().split()[-1]}
                    who = me_char = pool.CHARS.get(cid, '')
            except ValueError:
                pass
            mine_b = seats(m)[0]
            members = session_members(m)
            left = ([members[mine_b]] if mine_b in members else
                    [t for b, t in members.items() if b not in friend_bytes(args)])
            if len(left) == 1:
                labels.add(left[0].lower())
                me_gt = left[0]
                who = f'{who} ({left[0]})' if who else left[0]
        if hits is not None and c['type'] == 4 and args.wants:
            mine = wants_for(args.wants, labels)
        if mine and wanted(item, mine, names):
            hits.append(('you', item, True))
        elif mine and not c['snap']:
            cap = args.luck_cap if args.luck_cap is not None else luck + 40
            try:
                me = local_player(m)
                max_a, max_b = luck_accessories(m, character_id(m, me) if me else None)
            except ValueError:
                max_a, max_b = 2, 2
            if args.acc_cap:
                max_a, max_b = min(max_a, args.acc_cap[0]), min(max_b, args.acc_cap[1])
            every = luck_recipes(c, rng, mine, names, max_a, max_b)
            recipes = [(it, a, b, lo, min(hi, cap)) for it, a, b, lo, hi in every if lo <= cap]
            if recipes:
                hits.append(('you', recipes[0][0], 'reach:' + fmt_recipes(recipes, names, luck, acc_a, acc_b)))
            elif every:
                hits.append(('you', every[0][0], f'far:only at LCK {min(r[3] for r in every)}+, above your cap of {cap}'))
        rows = getattr(args, 'rows', None)
        if rows is not None and c['type'] == 4:
            rows.append({'key': 'you', 'labels': sorted(labels),
                         'settings': SETTINGS['players'].get('you', {}),
                         'player': me_gt or 'You', 'char': me_char or '?',
                         'luck': f'{luck}' + (' (locked)' if c['snap'] else ''), 'want': ', '.join(mine) or 'None',
                         'manip': manip_cell(hits[h0:] if hits is not None else [], mine) or '-',
                         'drop': (names.get(item, hex(item)) if item else '?') if c['count'] else 'chest still loading',
                         'odds': drop_odds(c, weighted, names) if c['count'] else []})
        lines.append(f'{CHEST_TYPES.get(c["type"], c["type"])} chest @ {h:08X}  state {c["state"]}  '
                     f'rng[{c["key"]}]={rng:08X}  LCK {luck} acc {acc_a}/{acc_b} ({src})')
        lines.append(f'  >>> {"you, " + who + ", " if who else ""}WILL DROP: {fmt_item(item, names)}  via {how}  (roll {r}/{tot})')
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


WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
WEB_FILES = {'/': ('index.html', 'text/html'), '/index.html': ('index.html', 'text/html'),
             '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
LATEST = {'state': 'waiting', 'note': 'starting', 'rows': []}   # newest record, for the web page
import threading
SETTINGS_LOCK = threading.Lock()


def serve(port):
    """Serve web/ and /state.json (the newest record) on localhost in a background thread."""
    import http.server, threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split('?')[0]
            if path == '/state.json':
                body, ctype = json.dumps({**LATEST, 'now': time.time()}).encode(), 'application/json'
            elif path in WEB_FILES:
                name, ctype = WEB_FILES[path]
                try:
                    with open(os.path.join(WEB_DIR, name), 'rb') as fh:
                        body = fh.read()
                except OSError:
                    return self.send_error(404)
            else:
                return self.send_error(404)
            self.send_response(200)
            self.send_header('Content-Type', ctype + '; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            # Only this page and the sidebar may write: a JSON body (other sites can't send one
            # without a CORS preflight, which this server never answers) from a localhost origin.
            origin = self.headers.get('Origin', '')
            if (self.path not in ('/set', '/quit') or 'application/json' not in self.headers.get('Content-Type', '')
                    or not origin.startswith(('http://localhost:', 'http://127.0.0.1:'))):
                return self.send_error(403)
            if self.path == '/quit':    # the page's Stop button: answer, then end the whole watcher
                import sys
                body = json.dumps({'ok': True, 'said': 'Watcher stopped.'}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
                print('stopped from the web page', file=sys.stderr, flush=True)
                threading.Timer(0.3, os._exit, (0,)).start()
                return
            try:
                req = json.loads(self.rfile.read(min(int(self.headers.get('Content-Length', 0)), 4096)))
                with SETTINGS_LOCK:
                    said = apply_setting(req, LATEST.get('rows', []))
            except (ValueError, TypeError, AttributeError) as e:
                body = json.dumps({'ok': False, 'said': str(e)}).encode()
                self.send_response(400)
            except Exception as e:      # a bug: still answer, so the page and sidebar can say why
                body = json.dumps({'ok': False, 'said': f'watcher error: {e!r}'}).encode()
                self.send_response(500)
            else:
                body = json.dumps({'ok': True, 'said': said}).encode()
                self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    def run():
        # Keep trying: a watcher being replaced can hold the port for a moment.
        import sys
        warned = False
        while True:
            try:
                srv = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
                break
            except OSError as e:
                if not warned:
                    print(f'web page waiting for port {port}: {e}', file=sys.stderr, flush=True)
                    warned = True
                time.sleep(2)
        srv.serve_forever()

    threading.Thread(target=run, daemon=True).start()


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
    ap.add_argument('--serve', type=int, metavar='PORT',
                    help='also serve a live HTML table at http://localhost:PORT')
    ap.add_argument('--json', action='store_true',
                    help='one JSON record per refresh (state, and a row per player) for the sidebar pane')
    ap.add_argument('--all', action='store_true', help='show every unopened chest, not just the boss chest')
    ap.add_argument('--luck', type=float, help='override LCK used before the game locks it in')
    ap.add_argument('--acc-a', type=int, help='override count of luck accessory A (0-2); default: read from equipment')
    ap.add_argument('--acc-b', type=int, default=0, help='override count of luck accessory B (with --acc-a)')
    ap.add_argument('--friend', action='append', help="friend as CHAR or CHAR:BYTE, repeatable, for when the session's members can't be read")
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--interval', type=float, default=0.5)
    ap.add_argument('--rescan', type=float, default=5.0, help='seconds between object rescans')
    ap.add_argument('--lo', type=lambda s: int(s, 16), default=0x40000000, help='scan start (hex)')
    ap.add_argument('--hi', type=lambda s: int(s, 16), default=0x50000000, help='scan end (hex)')
    ap.add_argument('--want', action='append',
                    help="target item name (substring, any case), repeatable; 'Richter: Megingiord' targets "
                         "one player ('you', a character name or a gamertag); the web page can change it")
    ap.add_argument('--max-acc', type=parse_acc,
                    help="most luck items you own, as A/B (type A: Thief Ring, Ebisu Raiment; type B: Soul Eater Ring); "
                         "default: 2/2 limited by what your character can equip")
    ap.add_argument('--max-luck', type=int,
                    help='highest LCK you can reach with gear swaps (default: current + 40)')
    args = ap.parse_args()

    args.warn = ''
    SETTINGS['wants'] = [split_who(w) for w in args.want or []]
    you = {}
    if args.max_luck is not None:
        you['maxluck'] = str(args.max_luck)
    if args.max_acc:
        you['maxacc'] = '%d/%d' % (args.max_acc if isinstance(args.max_acc, tuple) else parse_acc(args.max_acc))
    if you:
        SETTINGS['players']['you'] = you
    if args.serve:
        serve(args.serve)
    names = load_names()
    m, chests, last_scan, last_out = None, [], 0.0, None
    bases, rang = set(), None    # stage seeds seen this run (= reroll count); base the bell last rang on
    last_emit, changed, last_text = 0.0, time.time(), None

    def show(state, out, rows=()):
        """Print a frame when it changed; in --json mode also a heartbeat every few seconds."""
        nonlocal last_out, last_text, last_emit, changed
        flat = [o.replace('\a', '') for o in out]
        if args.json or args.serve:
            key = [state, flat, list(rows), args.warn]
            if key != last_out:
                changed, last_out = time.time(), key
            elif time.time() - last_emit < 3:
                return
            head = flat[0] if state == 'live' and len(flat) > 1 and not flat[0].startswith('BOSS') else ''
            global LATEST
            LATEST = ({'state': state, 'note': flat[0] if state != 'live' else '',
                           'head': head.split('\n')[0], 'warn': args.warn,
                           'changed': changed, 'at': time.time(), 'rows': list(rows)})   # swapped whole: the web thread reads it
            if args.json:
                print(json.dumps(LATEST), flush=True)
            last_emit = time.time()
        if not args.json and flat != last_text:
            print('\033[2J\033[H' + time.strftime('%H:%M:%S') + '\n' + '\n'.join(out), flush=True)
            last_text = flat

    while True:
        now = time.time()
        if now - last_scan > args.rescan:
            # Attach, and reattach when the game restarts: the old process's memory goes stale.
            try:
                pid = Mem._find_pid()
            except SystemExit as e:
                if args.once:
                    raise
                m, chests = None, []
                show('waiting', [f'{e}; waiting for it to start'])
                time.sleep(max(args.interval, 2))
                continue
            if m is None or m.pid != pid:
                try:
                    m = Mem(pid)
                except (SystemExit, OSError):
                    m = None    # just started; its memory isn't mapped yet
                    show('waiting', ['the game is starting; waiting for its memory'])
                    time.sleep(max(args.interval, 2))
                    continue
            chests, last_scan = scan_chests(m, args.lo, args.hi), now
        ensure_names(m, names)
        with SETTINGS_LOCK:
            args.wants, args.luck_cap, args.acc_cap = want_list(args)
        hits, args.rows, args.warn = [], [], ''

        out = report(m, chests, args, names, hits) or ['no unopened boss chest in this stage yet']
        if args.wants and out[0].startswith('BOSS'):
            try:
                base = m.u32(m.u32(HANDLE_TABLE) + SEED_BASE)
            except ValueError:
                base = None
            bases.add(base)
            sure = [h for h in hits if h[2] is True]
            reach = [(h[0], h[2][6:]) for h in hits if isinstance(h[2], str) and h[2].startswith('reach:')]
            far = [h[2][4:] for h in hits if isinstance(h[2], str) and h[2].startswith('far:')]
            hits = [h for h in hits if h[2] is False]
            if sure:
                who = ', '.join(f'{"you get" if nm == "you" else nm + " gets"} {names.get(it, hex(it))}' for nm, it, _ in sure)
                head = f'*** TARGET HIT (load #{len(bases)}): {who}. Open the chest!'
                if not args.once and rang != base:
                    head += '\a'
                    rang = base
            elif reach:
                who = ' | '.join(text if nm == 'you' else f'{nm} should: {text}' for nm, text in reach)
                head = f'+++ TARGET REACHABLE (load #{len(bases)}): change gear BEFORE touching the chest. ' + who
            elif hits:
                who = ', '.join(f'{nm} might get {names.get(it, hex(it))}' for nm, it, _ in hits)
                head = f'??? TARGET POSSIBLE (load #{len(bases)}): {who}, depending on their Luck'
            else:
                head = f'--- no target this load (#{len(bases)}); reload. Want: {fmt_wants(args.wants)}'
                if far:
                    head += f' ({"; ".join(far)})'
            unknown = [w for _, w in args.wants if not any(squash(w) in squash(v) for v in names.values())]
            if unknown and names:
                args.warn = f'No item is named like: {", ".join(unknown)}'
                head += f'\n    (no item name contains: {", ".join(unknown)})'
            out = [head] + out
        if args.once:
            if args.json:
                print(json.dumps({'state': 'live' if args.rows else 'no chest', 'rows': args.rows}))
            else:
                print('\n'.join(out).replace('\a', ''))
            return
        show('live' if args.rows else 'no chest', out, args.rows)
        time.sleep(args.interval)


if __name__ == '__main__':
    main()
