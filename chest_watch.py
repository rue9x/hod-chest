"""Live boss-chest drop predictor for Castlevania: Harmony of Despair Recomp.

Reads guest RAM through xenia's /dev/shm backing file (no ptrace/root needed),
finds treasureBox objects and replays the game's own roll (guest fn 823AA6D8).

Usage: python3 chest_watch.py [--all] [--luck N] [--once] [--interval S]
"""
import argparse, json, math, os, re, struct, threading, time

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
    for _ in range(64):         # a balanced tree this size is far shallower; a loop means freed memory
        if m.u8(node + 0x15):
            break
        k = m.s32(node + 0xC)
        if k == key:
            obj = m.u32(node + 0x10)
            return obj, m.u32(obj + 4)
        node = m.u32(node + 8) if k < key else m.u32(node)
    else:
        raise ValueError('RNG registry walk did not end (being torn down?)')
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
    lk = f32(m, s + 0x5C) + f32(m, s + 0x70) + f32(m, s + 0xF8)
    # Mid-load the floats can be garbage (NaN, inf, huge): treat that as unknown.
    return lk if math.isfinite(lk) and 0 <= lk <= 999 else None


GURD_DB = 0x8291D984      # armor/accessory database global (base + DLC files)


def armor_record(m, idx):
    """Guest fn 8246D8E8 for GURD: record address of armor index `idx`, or None."""
    o = m.u32(GURD_DB)
    base = m.u32(m.u32(o + 4) + 0x10)
    if idx < m.u32(base + 8):
        return base + 0x14 + idx * 0x4E
    for e in range(m.u32(o + 0x10), m.u32(o + 0x14), 0x3C):
        try:                    # a DLC that isn't loaded (or owned) is skipped, not fatal
            h = m.u32(e + 0x2C)
            f = m.u32(h + 0x10) if h else 0
            if not f:
                ARMOR_SKIPS[0] += 1
            elif m.u32(f + 0xC) <= idx < m.u32(f + 0xC) + m.u32(f + 8):
                return f + 0x14 + (idx - m.u32(f + 0xC)) * 0x4E
        except ValueError:
            ARMOR_SKIPS[0] += 1
    return None


def estimate_accessories(m, p=None):
    """Guest fns 823AAC68 / 823AAD18: equipped slots 2-7 with armor effect 0x0E / 0x0A, max 2 each."""
    p = p or local_player(m)
    eq = m.u32(p + 0x9C8) if p else 0
    if not eq:                  # no player or equipment object: unknown, which is not "none worn"
        raise ValueError('no equipment to read luck items from')
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
    if not b <= e <= b + 4 * 512:   # a chest table is a few dozen entries; more means a stale object
        raise ValueError(f'chest {h:08X}: bad entry range {b:08X}-{e:08X}')
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
ARMOR_SKIPS = [0]   # DLC armour databases armor_record skipped: answers built across a skip expire


def cached(cache, key):
    """A cached answer: kept for good when it was built with every database read, else for 10 s."""
    hit = cache.get(key)
    return hit[0] if hit and (hit[1] or time.time() - hit[2] < 10) else None


def keep(cache, key, value, skips_before):
    cache[key] = (value, ARMOR_SKIPS[0] == skips_before, time.time())


def luck_accessories(m, cid):
    """(max type-A, max type-B) luck items character `cid` can wear: 2 each, or fewer if it can equip fewer kinds
    (each item counts once; two of the same item still count twice, so any equippable kind allows 2)."""
    hit = cached(_luck_acc_cache, cid)
    if hit is not None:
        return hit
    import pool
    worn = {0xE: set(), 0xA: set()}     # effect -> slots an equippable item of that kind goes in
    failed, skips = False, ARMOR_SKIPS[0]
    for i in range(BASE[5] - BASE[4]):
        try:
            rec = armor_record(m, i)
            eff = m.u16(rec + 0xE) if rec else 0
            if eff in worn and (not cid or pool.allowed(m, BASE[4] + i, cid)):
                worn[eff].add(m.u16(rec + ARMOR_SLOT))
        except ValueError:
            failed = True       # armor (or DLC) data still mapping in: don't keep a partial answer
    if failed:      # some armour unreadable: "can't wear any" would be a guess, so say unknown
        raise ValueError('armour data not fully readable yet')
    # Two accessory slots take two rings; any other slot (Ebisu Raiment is body armour) takes one.
    a, b = (min(2, sum(2 if sl == 5 else 1 for sl in worn[e])) for e in (0xE, 0xA))
    _body_a[cid] = int(any(sl != 5 for sl in worn[0xE]))       # a type-A item outside the accessory slots
    keep(_luck_acc_cache, cid, (a, b), skips)
    return a, b


_body_a = {}    # cid -> 1 if the character can wear a type-A luck body armour (Ebisu Raiment), else 0


def wearable(a, b, body=1):
    """Whether `a` type-A and `b` type-B luck items fit at once: rings take the two accessory slots, and
    one type-A item can be body armour instead (Ebisu Raiment) when the character can wear it."""
    return b + max(0, a - body) <= 2


def local_cid(m):
    """Your character id, or None while it can't be read."""
    try:
        me = local_player(m)
        return character_id(m, me) if me else None
    except ValueError:
        return None


def body_slot(m, cid):
    """1 when `cid` can wear a type-A luck body armour, 0 when it can't; 1 (permissive) when unknown."""
    try:
        luck_accessories(m, cid)
    except ValueError:
        pass
    return _body_a.get(cid, 1)


def luck_recipes(c, rng, wants, names, max_a=2, max_b=2, body=1):
    """For this chest's fixed RNG state: [(item, acc_a, acc_b, lo, hi)] Luck ranges that drop a target."""
    key = (rng, c['count'], tuple(c['entries']), tuple(wants), max_a, max_b, len(names), body)
    if key in _recipe_cache:
        return _recipe_cache[key]
    out = []
    for a in range(max_a + 1):
        for b in range(max_b + 1):
            if not wearable(a, b, body):        # more luck items than there are slots for
                continue
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


_names = {'tried': 0.0, 'done': None, 'problems': None}   # ensure_names' progress


def ensure_names(m, names, every=10.0):
    """Build item_names.json from the player's own game (nothing shipped), at most once every `every`
    seconds until done. A full file on disk counts as done. A build is only saved once it is complete,
    or once two tries in a row hit the same problems (DLC the player doesn't own stays missing, rather
    than DLC that hasn't loaded yet). Until then the names found are used but not saved."""
    try:                                # item_names.json rewritten (gen_item_names.py after new DLC): use it
        mtime = os.stat(NAMES_FILE).st_mtime
    except OSError:
        mtime = None
    if 'mtime' not in _names or mtime != _names['mtime']:
        if mtime is not None and 'mtime' in _names:     # written since the last look (or newly made)
            fresh = load_names()
            if len(fresh) >= len(names):
                names.clear()
                names.update(fresh)
        _names['mtime'] = mtime
    if _names['done'] is None:          # first call: what's on disk
        _names['done'] = len(names) > 200
    if _names['done'] or time.time() - _names['tried'] < every:
        return names
    _names['tried'] = time.time()
    try:
        import gen_item_names
        built, _, problems = gen_item_names.build(m)
        if len(built) > 200:            # item databases are loaded (we're in a stage)
            if len(built) >= len(names):
                names.clear()
                names.update(built)
                # only ever save a build at least as full as what's in hand
                if not problems or problems == _names['problems']:
                    gen_item_names.write(built)
                    _names['done'] = True
            _names['problems'] = problems
    except ValueError:      # outside a stage the item databases aren't in memory: expected, retried
        pass
    except Exception as e:  # noqa: BLE001 - incl. ImportError if txrc.py / gen_item_names.py is missing
        msg = f'item names not built: {type(e).__name__}: {e}'
        if msg != _names.get('said'):       # once, not every 10 seconds
            import sys
            print(msg, file=sys.stderr, flush=True)
            _names['said'] = msg
    return names


def fmt_item(item, names):
    nm = names.get(item)
    cat = category(item)
    return f'{cat:04X}:{item - BASE[cat]:04X} {nm or "?"}'


def scan_chests(m, lo, hi):
    pat = struct.pack('>I', TREASURE_VTABLE)
    return [a for a in m.search(pat, lo, hi) if a % 4 == 0]


SESSION = 0x829034E0 + 0x20     # online session object; members at +0x17C (slots 0x5F..)
MEMBER_VT = 0x82068DA8           # session member objects (every player, local and remote, seen in game)
SEED_BASE = 0x84                 # *(HANDLE_TABLE obj)+0x84: per-stage seed base, shared across players

# Every setting lives in memory only and resets when the watcher restarts. The web page (and the
# sidebar's /chest command, through the same page server) changes it with POST /set.
#   wants:   [(who or None, item name)]; who is a lowercase player label, None means anyone
#   players: {player key, lowercase ('you' or a gamertag): {maxluck, maxacc, owns, base, luck}}
SETTINGS = {'wants': [], 'players': {}, 'own': set()}
#   own:     player keys whose targets were picked one by one: targets for anyone don't apply to them
PLAYER_FIELDS = ('maxluck', 'maxacc', 'owns', 'base', 'luck', 'acc', 'accb')


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


def set_player_want(key, labels, item, everyone, items=()):
    """Make `item` (or nothing, if empty) the target of the player `key`: exactly that item when it's
    one of `items` (their chest's list), else as typed, a comma list of names. Targets for anyone stay
    for everyone else; this player stops taking them. A label two players share (both playing
    Richter) is left alone, so one player's change never drops another's."""
    others = {l.lower() for k, ls in everyone if k.lower() != key.lower() for l in list(ls) + [k]}
    mine = ({l.lower() for l in labels} | {key.lower()}) - others
    keep = [(who, name) for who, name in SETTINGS['wants'] if who is None or who not in mine]
    if item and any(squash(shown(item)) == squash(i) for i in items):
        keep.append((key.lower(), '=' + shown(item)))   # picked from the list: that exact item
    elif item:                                          # a typed target picked again: keep it as typed
        keep += [(key.lower(), t.strip()) for t in item.split(',') if squash(t).lstrip('=')]
    SETTINGS['wants'] = keep
    SETTINGS['own'] = SETTINGS['own'] | {key.lower()}


def apply_setting(req, rows):
    """One change from POST /set: {field, value, key?}. Returns what it did, or raises ValueError."""
    field, value = str(req.get('field', '')), str(req.get('value') or '').strip()
    if field == 'wants':        # the whole list: 'Item, Gamertag: Item'
        SETTINGS['wants'] = [split_who(w) for w in value.split(',') if squash(split_who(w)[1]).lstrip('=')]
        SETTINGS['own'] = set()     # a fresh list: targets for anyone apply to everyone again
        return f'Desired drop: {fmt_wants(SETTINGS["wants"])}' if SETTINGS['wants'] else 'Desired drop cleared (None).'
    key = str(req.get('key') or 'you')
    if field == 'want':         # one player's only target
        row = next((r for r in rows if r.get('key', '').lower() == key.lower()), None)
        if row is None and key.lower() == 'you':
            row = {'key': 'you', 'labels': ['you', 'me'], 'player': 'You'}
        if row is None:
            raise ValueError(f'no player {key!r} in this stage')
        set_player_want(row['key'], row.get('labels', []), value, [(r['key'], r.get('labels', [])) for r in rows],
                        row.get('items', []))
        return f'{row["player"]}: desired drop {value or "None"}'
    if field not in PLAYER_FIELDS:
        raise ValueError(f'unknown setting {field!r}')
    if value:
        if field == 'maxacc':
            a, b = parse_acc(value)
            value = f'{a}/{b}'
        elif field in ('acc', 'accb'):
            if value not in ('0', '1', '2'):
                raise ValueError(f'{field} is how many '
                                 f'{"Thief Rings / Ebisu Raiments" if field == "acc" else "Soul Eater Rings"}'
                                 ' they wear: 0, 1 or 2')
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


def char_targets(wants):
    """Whether any target is aimed at a character ('Charlotte: Venus Brooch'). Those can't be checked
    for a player whose character isn't known yet."""
    import pool
    chars = {v.lower() for v in pool.CHARS.values()} | {v.lower().split()[-1] for v in pool.CHARS.values()}
    return any(who in chars for who, _ in wants)


def wants_for(wants, labels, own=None):
    """Target names that apply to a player known by any of `labels`. Targets for anyone skip players
    whose own target was picked on the page (`own`: those players' keys, from the same snapshot as
    `wants`; default the live settings)."""
    own = bool((SETTINGS['own'] if own is None else own) & set(labels))
    return [nm for who, nm in wants if (who is None and not own) or who in labels]


def fmt_wants(wants):
    return ', '.join(f'{who}: {shown(nm)}' if who else shown(nm) for who, nm in wants)


def parse_acc(text):
    a, _, b = text.strip().partition('/')
    return max(0, min(2, int(a))), max(0, min(2, int(b or 0)))


def squash(text):
    """Lowercase with spaces and apostrophes dropped: 'Valmanway+1' matches 'Valmanway +1',
    "Dracula's Soul" matches 'Dracula Soul'."""
    return ''.join(text.lower().replace('\u2019', "'").replace("'s", '').replace("'", '').split())


def forms(text):
    """Both squashed forms of a name: without a possessive 's ("Dracula's Soul" ~ 'Dracula Soul'), and
    with the s kept ("Death's Robe" ~ 'Deaths Robe')."""
    keep_s = ''.join(text.lower().replace('\u2019', "'").replace("'", '').split())
    return {squash(text), keep_s}


def matches(target, name):
    """Whether item `name` satisfies `target`: a substring ('Valmanway' matches 'Valmanway +1'), or
    with a leading '=' (a pick from the page's list) that exact item only. '' matches nothing."""
    exact = squash(target).startswith('=')
    ts = {t.lstrip('=') for t in forms(target)} - {''}
    ns = forms(name or '') - {''}
    return any((t == n if exact else t in n) for t in ts for n in ns)


def for_page(targets, names):
    """Targets for the page and pane: a hex label picked before names loaded ('=0x46') goes by the
    item's name once known ('=Badelaire'), so the page's matching and dropdown find it."""
    out = []
    for t in targets:
        exact, body = t.startswith('='), t.lstrip('=').strip()
        if re.fullmatch(r'0x[0-9a-fA-F]+', body) and int(body, 16) in names:
            body = names[int(body, 16)]
        out.append(('=' if exact else '') + body)
    return out


def shown(target):
    """A target as the page shows it, without the exact-match '='."""
    return target.lstrip('=').strip()


def wanted(item, wants, names):
    """Whether `item` is one of `wants`. Unnamed items go by the hex label the page lists them as."""
    # also by hex label, so a pick made before the names loaded keeps matching after they do
    return bool(item) and any(matches(w, names.get(item) or hex(item)) or matches(w, hex(item)) for w in wants)


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
            if not mem or m.u32(mem) != MEMBER_VT:
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
    if local is None:   # can't tell which member is you (someone still loading): wait a tick
        return None, []
    return local, [(b, members[b], objs.get(b)) for b in order if b != local]


def friend_overrides():
    """{gamertag, lowercase: options} set on the page for friends."""
    return {k: v for k, v in SETTINGS['players'].items() if k != 'you'}


def chest_state_from_seed(seed):
    """Chest init (guest 823A8540): seed the stream, draw n = rand(10,30), then n more steps."""
    n, st = rand_range(seed, 10, 30)
    for _ in range(n):
        st = lcg_next(st)
    return st


def friend_list(args):
    """[(character, seed byte, ...)] from --friend 'Charlotte:0x21 luck=12' (or 'Getsu Fuma:0x21'), for when
    the game's member list can't be read. The byte is the friend's connection id: (reuse counter << 5) | slot."""
    import pool
    by_name = {v.lower(): v for v in pool.CHARS.values()}
    out = []
    for item in args.friend or []:
        words = item.split()
        opts = dict(w.split('=', 1) for w in words if '=' in w)
        rest = ' '.join(w for w in words if '=' not in w)
        name, sep, byte = rest.rpartition(':')
        if not sep:             # no ':': a byte may still follow the name ('Charlotte 0x21')
            head, _, last = rest.rpartition(' ')
            name, byte = (head, last) if head and last[:1].isdigit() else (rest, '')
        name = name.strip()
        if not name:
            raise ValueError(f'no character in --friend {item!r}')
        out.append((by_name.get(name.lower(), name), int(byte, 0) if byte.strip() else None,
                    int(opts['luck']) if 'luck' in opts else None,
                    parse_acc(opts['acc'])[0] if 'acc' in opts else None,     # 0-2, as the game allows
                    {**{k: opts[k] for k in ('maxluck', 'owns', 'base') if k in opts},
                     **({'maxacc': '%d/%d' % parse_acc(opts['maxacc'])} if 'maxacc' in opts else {}),
                     **({'accb': str(parse_acc(opts['acc'])[1])} if '/' in opts.get('acc', '') else {})}))
    return out


def chest_snapshot(m, h, c):
    """Everything needed to replay this chest for any player later (stored by the logger)."""
    rec, size = m.u32(h + 0x198), m.u32(h + 0x194)
    if size > 0x1000:
        raise ValueError(f'chest {h:08X}: bad record size {size:#x}')
    table = [list(struct.unpack('>II', m.read(rec + o, 8))) for o in range(0x10, size, 8)]
    if any(t[0] >> 16 >= len(BASE) - 1 for t in table):     # categories 0-15; BASE[16] only ends 15
        raise ValueError(f'chest {h:08X}: record still loading (bad item category)')
    return {'key': c['key'], 'base': m.u32(m.u32(HANDLE_TABLE) + SEED_BASE),
            'pools': [x for x in (m.s32(rec + 8), m.s32(rec + 0xC)) if x >= 0],
            'table': table}


CHESTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chests.jsonl')


_last_saved = [None]     # (key, base) of the last chest written, so the log is read only once


def save_snapshot(snap):
    """Append the chest to chests.jsonl once (dedup on key+base) for friend_byte.py."""
    if _last_saved[0] is None:          # first call: the newest chest already in the log
        _last_saved[0] = ()
        try:
            with open(CHESTS_FILE, 'rb') as fh:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - 65536))
                tail = fh.read().decode('utf-8', 'replace').strip().rsplit('\n', 1)[-1]
            prev = json.loads(tail) if tail else None
            if prev:
                _last_saved[0] = (prev['key'], prev['base'])
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if _last_saved[0] == (snap['key'], snap['base']):
        return
    try:                                # a cut-off last line (killed mid-write) gets its own line
        with open(CHESTS_FILE, 'rb') as fh:
            fh.seek(-1, os.SEEK_END)
            lead = '' if fh.read(1) == b'\n' else '\n'
    except OSError:                     # missing or empty
        lead = ''
    with open(CHESTS_FILE, 'a', encoding='utf-8') as fh:
        fh.write(lead + json.dumps(snap) + '\n')
    _last_saved[0] = (snap['key'], snap['base'])


_pool_cache = {}


def build_pool(m, snap, cid):
    """pool.build for a chest snapshot and character, cached: it reads every item database, and a
    friend's row, prediction and gear suggestions all need the same one each refresh."""
    import pool
    key = (tuple(tuple(t) for t in snap['table']), tuple(snap['pools']), cid)
    hit = _pool_cache.get(key)
    if hit and (hit[1] or time.time() - hit[2] < 10):
        return hit[0]
    skipped = pool.SKIPPED[0]
    built = pool.build(m, [tuple(t) for t in snap['table']], snap['pools'], cid)
    # Built with a DLC database missing (still loading, or not owned): keep it for 10 s, then look again.
    if len(_pool_cache) > 32:
        _pool_cache.clear()
    _pool_cache[key] = (built, pool.SKIPPED[0] == skipped, time.time())
    return built


def predict_other(m, snap, cid, byte, luck_steps=5, lucks=None, accs=(0, 1, 2), acc_b=0, bs=(0, 1, 2), body=1):
    """acc_b None: the type-B count is unknown, so every count in `bs` is tried."""
    """{item: [luck values]} for a player with character id `cid` and seed byte `byte`."""
    import pool
    st = chest_state_from_seed((snap['base'] + snap['key'] + 1 + byte) & 0xFFFFFFFF)
    count, entries = build_pool(m, snap, cid)
    res = {}
    for luck in (lucks if lucks is not None else range(0, 301, luck_steps)):
        for acc in accs:
            for b in ((acc_b,) if acc_b is not None else bs):
                if not wearable(acc, b, body):
                    continue
                item = predict({'count': count, 'entries': list(entries)}, st, luck, acc, b)[0]
                res.setdefault(item, []).append(luck)
    return res


ARMOR_SLOT, ARMOR_LUCK = 0xC, 0x36     # GURD record: 1 helm, 2 body, 3 cloak, 4 boots, 5 accessory; LCK bonus
SLOT_NAMES = {1: 'head', 2: 'body', 3: 'cloak', 4: 'feet', 5: 'accessory'}
_outfit_cache = {}


def outfits(m, cid, names):
    """{(gear LCK, type-A, type-B): fewest luck items giving it} over every outfit of luck gear `cid`
    can equip, one of each item, two accessory slots. Slots left out hold anything without luck."""
    cache_key = (cid, len(names))   # names may still be loading: don't keep hex placeholders
    hit = cached(_outfit_cache, cache_key)
    if hit is not None:
        return hit
    import itertools, pool
    by_slot = {k: [] for k in SLOT_NAMES}
    failed, skips = False, ARMOR_SKIPS[0]
    for i in range(BASE[5] - BASE[4]):
        try:
            rec = armor_record(m, i)
            if not rec:
                continue
            lck, eff, slot = m.s16(rec + ARMOR_LUCK), m.u16(rec + 0xE), m.u16(rec + ARMOR_SLOT)
            if slot in by_slot and (lck > 0 or eff in (0xE, 0xA)) and pool.allowed(m, BASE[4] + i, cid):
                by_slot[slot].append((names.get(BASE[4] + i, hex(BASE[4] + i)), lck, eff == 0xE, eff == 0xA))
        except ValueError:
            failed = True
    none = [None]
    pick = lambda slot: none + by_slot[slot]
    # two accessory slots: none, one, or two different ones (owning every item means one of each;
    # two of the same ring is left to the Luck-range suggestions, capped by luck items owned)
    accs = [()] + [(x,) for x in by_slot[5]] + list(itertools.combinations(by_slot[5], 2))
    best = {}
    for gear in itertools.product(pick(1), pick(2), pick(3), pick(4), accs):
        items = [g for g in gear[:4] if g] + list(gear[4])
        key = (sum(g[1] for g in items), min(2, sum(g[2] for g in items)), min(2, sum(g[3] for g in items)))
        if key not in best or len(items) < len(best[key]):
            best[key] = [g[0] for g in items]
    if not failed:
        keep(_outfit_cache, cache_key, best, skips)
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


def roll_odds(c, weighted):
    """{item: chance 0-1} for the roll predict() makes: each weighted item w / R (R = max(total, 10000)),
    and the rest split evenly over every pool slot, an empty slot giving the fallback item."""
    n, extras = c['count'], c['entries'][c['count']:]
    total = sum(w for it, w in weighted if it and w)
    R = max(total, 10000)
    odds = {}
    if n:
        for it, w in weighted:
            if it and w:
                odds[it] = odds.get(it, 0) + w / R
    rest = 1.0 if n == 0 else (R - total) / R
    fallback = c['entries'][0][0] if c['entries'] and c['entries'][0][0] else 7
    if extras:
        for it, _ in extras:
            k = it or fallback
            odds[k] = odds.get(k, 0) + rest / len(extras)
    elif rest > 0:
        odds[fallback] = odds.get(fallback, 0) + rest
    return odds


def luck_runs(values):
    """'0-10, 50-60' for the Luck values an item drops at: each unbroken band, not one min-max."""
    vals, out = sorted(set(values)), []
    for v in vals:
        if out and v == out[-1][1] + 1:
            out[-1][1] = v
        else:
            out.append([v, v])
    return ', '.join(f'{a}' if a == b else f'{a}-{b}' for a, b in out)


def by_luck_text(res, names, luck_varies):
    """A drop that depends on what isn't known: 'by Luck: A (0-10, 50-60), B (11-49)', or, when only the
    luck items vary, 'by luck items: A, B'."""
    if luck_varies:
        return 'by Luck: ' + ', '.join(f'{names.get(it, hex(it))} ({luck_runs(v)})' for it, v in res.items())
    return 'by luck items: ' + ', '.join(names.get(it, hex(it)) for it in res)


def pool_items(c, names):
    """Every item this player's chest can give, by name: the page's desired-drop choices."""
    return sorted({names.get(it, hex(it)) for it, _ in c['entries'] if it})


def drop_odds(c, weighted, names):
    """[[item name, % chance]] for one player's chest, likeliest first."""
    out = [[names.get(it, hex(it)), round(100 * p, 1)] for it, p in roll_odds(c, weighted).items() if p > 0]
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


def friend_reach(m, snap, cid, byte, cname, luck, acc, limits, wants, names, acc_b=0):
    """Gear suggestions for a friend whose Luck is known, as hit tuples like the local player's."""
    import pool
    st = chest_state_from_seed((snap['base'] + snap['key'] + 1 + byte) & 0xFFFFFFFF)
    count, entries = build_pool(m, snap, cid)
    fc = {'count': count, 'entries': list(entries)}
    if limits.get('owns') == 'all' and 'base' in limits:
        try:
            found = outfit_reach(m, st, fc, cid, cname, int(limits['base']), luck, wants, names)
            if found:
                return found
        except ValueError:
            pass            # no outfit works (or unreadable): the Luck-range suggestions below still might
    try:
        max_a, max_b = luck_accessories(m, cid)
    except ValueError:
        max_a, max_b = 2, 2     # armour unreadable for now: limited by what they own, below
    try:
        if 'maxacc' in limits:
            la, lb = parse_acc(limits['maxacc'])
            max_a, max_b = min(max_a, la), min(max_b, lb)
        cap = int(limits['maxluck']) if 'maxluck' in limits else luck + 40
    except ValueError:
        cap = luck + 40
    every = luck_recipes(fc, st, wants, names, max_a, max_b, body_slot(m, cid))
    recipes = [(it, a, b, lo, min(hi, cap)) for it, a, b, lo, hi in every if lo <= cap]
    if recipes:
        return [(cname, recipes[0][0], 'reach:' + fmt_recipes(recipes, names, luck, acc, acc_b))]
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
    loose = []          # online: --friend entries whose byte matches no seat, matched by character below
    if seated:   # online: everyone in the session, in seat order
        bytes_ = {b for b, _, _ in seated}
        given = {f[1]: f for f in friends if f[1] in bytes_}
        loose = [f for f in friends if f[1] not in bytes_]
        friends = [given.get(b, ('auto', b, None, None, {})) for b, _, _ in seated]
        keys = [gt for _, gt, _ in seated]
    else:   # no readable member list: only the friends named with --friend
        # the same key the row is shown and saved under: their gamertag when the member list has it
        keys = [members.get(f[1] if f[1] is not None else 0x21 + n) or f'friend{n + 1}'
                for n, f in enumerate(friends)]
    # The page's settings for each friend (by gamertag, or friendN offline) go over what's known.
    over = friend_overrides()
    merged = []
    for (name, b, luck, acc, limits), key in zip(friends, keys):
        o = over.get(key.lower(), {})
        try:
            luck = int(o['luck']) if 'luck' in o else luck
            acc = int(o['acc']) if 'acc' in o else acc
        except ValueError:
            pass
        merged.append((name, b, luck, acc,
                       {**limits, **{k: o[k] for k in ('maxluck', 'maxacc', 'owns', 'base', 'accb') if k in o}}))
    friends = merged
    snap = chest_snapshot(m, h, c)
    by_name = {v.lower(): k for k, v in pool.CHARS.items()}
    try:
        others = [obj for _, obj, local in players(m) if not local]
    except ValueError:
        others = []
    out = []
    claimed = set()     # offline: player objects already matched to a friend
    for n, (cname, byte, luck, acc, limits) in enumerate(friends):
        if byte is None:   # no byte on the line: guess (host is 0x20, guests 0x21, 0x22.. in join order)
            byte = 0x21 + n
        cid = by_name.get(cname.lower())
        if seated:
            obj = seat_obj.get(byte)
        else:   # no member list: only an object that is clearly this friend
            obj = None
            for o in others:
                if o in claimed:        # two friends on the same character get one object each
                    continue
                try:
                    if cid and character_id(m, o) == cid:
                        obj = o
                        break
                except ValueError:
                    pass
            if obj is not None:
                claimed.add(obj)
            if obj is None and not cid and len(friends) == 1 and len(others) == 1:
                obj = others[0]
        read_luck = read_acc = None
        acc_b = None    # type-B luck items (Soul Eater Ring): read from the game; None until read
        if obj:
            try:
                det = character_id(m, obj)
                if det in pool.CHARS:
                    cid, cname = det, pool.CHARS[det]
                lk = estimate_luck(m, obj)
                if lk is not None:
                    read_luck = int(lk)
                read_acc, acc_b = estimate_accessories(m, obj)
            except ValueError:
                pass
        if loose and cid:
            # a --friend line given without this seat's byte: it applies to the player on its character
            for f in loose:
                if f[0].lower() == (pool.CHARS.get(cid, cname) or '').lower():
                    loose.remove(f)
                    luck = f[2] if luck is None else luck
                    acc = f[3] if acc is None else acc
                    limits = {**f[4], **limits}
                    break
        if luck is None and read_luck is not None:
            luck = read_luck
        if acc is None and read_acc is not None:
            acc = read_acc
        rows = getattr(args, 'rows', None) if hits is not None else None
        if not cid:
            if rows is not None:
                fl = [(members.get(byte) or f'friend{n + 1}').lower(), f'friend{n + 1}']
                theirs = wants_for(args.wants, set(fl), getattr(args, 'own', None))
                if theirs or char_targets(args.wants):
                    args.judged = False     # a target for this player can't be checked yet
                rows.append({'key': members.get(byte) or f'friend{n + 1}',
                             'labels': fl, 'targets': for_page(theirs, names),
                             'settings': SETTINGS['players'].get((members.get(byte) or f'friend{n + 1}').lower(), {}),
                             'player': members.get(byte) or f'friend{n + 1}', 'char': '?', 'luck': '?',
                             'want': ', '.join(shown(w) for w in for_page(theirs, names)) or 'None', 'manip': '-',
                             'drop': 'character not detected yet', 'odds': [],
                             'items': []})
            out.append(f'  friend #{n + 1}: character not detected yet' if cname == 'auto' else
                       f'  friend {cname}: unknown character (use one of {", ".join(pool.CHARS.values())})')
            continue
        if acc is None:     # owns none, or this character can't wear any: then 0 for sure
            try:
                if str(limits.get('maxacc', '')).startswith('0') or luck_accessories(m, cid)[0] == 0:
                    acc = 0
            except ValueError:
                pass
        if str(limits.get('accb', '')) in ('0', '1', '2'):
            acc_b = int(limits['accb'])     # set on the page or with --friend acc=A/B
        if acc_b is None and str(limits.get('maxacc', '')).endswith('/0'):
            acc_b = 0       # they own no Soul Eater Ring
        if acc_b is None:   # unread: certainly 0 for a character who can't wear a Soul Eater Ring
            try:
                if luck_accessories(m, cid)[1] == 0:
                    acc_b = 0
            except ValueError:
                pass
        top_a, top_b = 2, 2     # unknown counts: only what they own ('Luck items owned') and can equip
        try:
            if 'maxacc' in limits:
                top_a, top_b = parse_acc(limits['maxacc'])
            can_a, can_b = luck_accessories(m, cid)
            top_a, top_b = min(top_a, can_a), min(top_b, can_b)
        except ValueError:
            pass
        args_other = dict(luck_steps=1, lucks=[max(0, min(300, luck))] if luck is not None else None,
                          accs=(acc,) if acc is not None else tuple(range(top_a + 1)), acc_b=acc_b,
                          bs=tuple(range(top_b + 1)))
        res = predict_other(m, snap, cid, byte, body=body_slot(m, cid), **args_other)
        impossible = not res    # their luck items are set to more than fit at once: predict as set anyway
        if impossible:
            res = predict_other(m, snap, cid, byte, body=2, **args_other)
        tag = (f'byte {byte:#04x}' + (f', LCK {luck}' if luck is not None else '')
               + (f', acc {acc}/{"?" if acc_b is None else acc_b}' if acc is not None else ''))
        gt = members.get(byte)
        label = f'{cname} ({gt})' if gt else cname
        flabels = {cname.lower(), cname.lower().split()[-1], f'friend{n + 1}'} | ({gt.lower()} if gt else set())
        mine = wants_for(args.wants, flabels, getattr(args, 'own', None)) if hits is not None else []
        h0 = len(hits) if hits is not None else 0
        if mine:
            for it in res:
                if wanted(it, mine, names):
                    hits.append((label, it, len(res) == 1))
            sure = len(res) == 1 and wanted(next(iter(res)), mine, names)
            # Outfits need only the base Luck the user gave, not their current Luck or gear.
            outfit = limits.get('owns') == 'all' and str(limits.get('base', '')).isdigit()
            if not sure and ((len(res) == 1 and luck is not None) or outfit):
                at = luck if luck is not None else int(limits['base'])
                hits += friend_reach(m, snap, cid, byte, label, at, acc or 0, limits, mine, names, acc_b or 0)
        if rows is not None:
            drop = (names.get(next(iter(res)), hex(next(iter(res)))) if len(res) == 1 else
                    by_luck_text(res, names, luck is None))
            if impossible:
                drop += ' (their luck items are set to more than can be worn at once: check the settings)'
            count, entries = build_pool(m, snap, cid)
            fc = {'count': count, 'entries': list(entries)}
            st = chest_state_from_seed((snap['base'] + snap['key'] + 1 + byte) & 0xFFFFFFFF)
            weighted = predict(fc, st, max(0, min(300, luck or 0)), acc or 0, acc_b or 0)[4]
            manip = manip_cell(hits[h0:], mine) or '-'
            if manip.startswith('maybe') and luck is not None:
                manip = 'maybe (luck items unknown)'
            rows.append({'key': gt or f'friend{n + 1}', 'labels': sorted(flabels),
                         'settings': SETTINGS['players'].get((gt or f'friend{n + 1}').lower(), {}),
                         'player': gt or f'friend{n + 1}', 'char': cname,
                         'luck': str(luck) if luck is not None else '?', 'want': ', '.join(shown(w) for w in for_page(mine, names)) or 'None',
                         'targets': for_page(mine, names),
                         'manip': manip, 'drop': drop,
                         # odds at their Luck; none when it's unknown rather than odds for Luck 0
                         # only when both are known: otherwise these would be odds for Luck 0 / no luck items
                         'odds': (drop_odds(fc, weighted, names)
                                  if luck is not None and acc is not None and acc_b is not None else []),
                         'items': pool_items(fc, names)})
        if len(res) == 1:
            out.append(f'  friend {label} [{tag}]: WILL DROP {fmt_item(next(iter(res)), names)}')
        else:
            parts = ', '.join(f'{names.get(it, hex(it))} (LCK {luck_runs(v)})' for it, v in res.items())
            out.append(f'  friend {label} [{tag}]: depends on their Luck/accessories: {parts}')
    return out


def report(m, chests, args, names, hits=None):
    lines = []
    try:
        est = None if args.luck is not None else estimate_luck(m)
    except ValueError:
        est = None
    found = []
    for h in chests:
        try:
            if m.u32(h) != TREASURE_VTABLE:
                continue
            c = read_chest(m, h)
        except ValueError:
            continue
        if c['state'] in OPENED_STATES or (not args.all and c['type'] != 4):
            continue
        found.append((h, c))
    # After a reload the previous load's chest can linger in freed memory with the same key. Keep
    # one chest per key, preferring a touched one: rerolling reloads without touching, so a leftover
    # is most likely untouched, and a touched chest's Luck is locked by the game itself.
    # (Not yet checked in game: what a leftover looks like, and whether one with the key of an
    # already-opened chest can show up as unopened.)
    keep = {}
    for h, c in found:
        if c['key'] not in keep or (c['snap'] and not keep[c['key']][1]['snap']):
            keep[c['key']] = (h, c)
    for h, c in found:
        if keep[c['key']][0] != h:
            continue
        try:
            obj, rng = rng_state(m, c['key'])
        except ValueError:
            continue    # its RNG stream is being torn down or not mapped yet
        if rng is None:
            lines.append(f'chest {h:08X}: RNG stream {c["key"]} not found')
            rows = getattr(args, 'rows', None)
            if rows is not None and c['type'] == 4:     # still a boss chest here: say so, not "no chest"
                rows.append({'key': 'you', 'labels': ['you', 'me'], 'settings': SETTINGS['players'].get('you', {}),
                             'player': 'You', 'char': '?', 'luck': '?', 'want': 'None', 'targets': [],
                             'manip': '-', 'drop': "can't read its roll yet (RNG stream not found)",
                             'odds': [], 'items': []})
            continue
        luck_known = acc_known = True
        # 3: a stale object's "locked" values can be garbage; only trust them when they're sane
        if c['snap'] and not (0 <= c['luck'] <= 999 and 0 <= c['acc_a'] <= 2 and 0 <= c['acc_b'] <= 2):
            c = {**c, 'snap': 0}
        if c['snap']:
            luck, acc_a, acc_b, src = c['luck'], c['acc_a'], c['acc_b'], 'locked in by game'
        else:
            luck_known = args.luck is not None or est is not None
            luck = args.luck if args.luck is not None else (est or 0)
            luck = max(0, min(300, int(luck)))
            try:
                acc_a, acc_b = (args.acc_a, args.acc_b) if args.acc_a is not None else estimate_accessories(m)
            except ValueError:
                acc_a, acc_b, acc_known = 0, 0, False
            src = ('override' if args.luck is not None else 'estimated from player' if luck_known
                   else "UNKNOWN (couldn't read it; showing Luck 0)")
            if not acc_known:
                src += "; luck items UNKNOWN (showing none)"
        item, how, r, tot, weighted = predict(c, rng, luck, acc_a, acc_b)
        mine, labels, who, me_char, me_gt = [], {'you', 'me'}, '', '', ''
        h0 = len(hits) if hits is not None else 0
        if c['type'] == 4:
            try:
                me = local_player(m)
                cid = character_id(m, me) if me else None
                import pool
                if cid in pool.CHARS:
                    labels |= {pool.CHARS[cid].lower(), pool.CHARS[cid].lower().split()[-1]}
                    who = me_char = pool.CHARS[cid]
            except ValueError:
                pass
            mine_b = seats(m)[0]
            members = session_members(m)
            # Every member is listed, so without your seat only a session of one names you for sure.
            left = ([members[mine_b]] if mine_b in members else
                    list(members.values()) if len(members) == 1 else [])
            if len(left) == 1:
                labels.add(left[0].lower())
                me_gt = left[0]
                who = f'{who} ({left[0]})' if who else left[0]
        if hits is not None and c['type'] == 4 and args.wants:
            mine = wants_for(args.wants, labels, getattr(args, 'own', None))
        certain = luck_known and acc_known
        by_luck = {}            # Luck or luck items unknown: what each possibility would drop
        if not certain:
            if acc_known:
                accs = [(acc_a, acc_b)]
            else:       # every count you could be wearing: no more than you own or can equip
                top_a, top_b = args.acc_cap or (2, 2)
                try:
                    me = local_player(m)
                    can_a, can_b = luck_accessories(m, character_id(m, me) if me else None)
                    top_a, top_b = min(top_a, can_a), min(top_b, can_b)
                except ValueError:
                    pass
                body = body_slot(m, local_cid(m))
                accs = [(a, b) for a in range(top_a + 1) for b in range(top_b + 1) if wearable(a, b, body)]
            for lk in (range(301) if not luck_known else [luck]):
                for a, b in accs:
                    by_luck.setdefault(predict(c, rng, lk, a, b)[0], []).append(lk)
        judge = mine if c['entries'] else []    # an empty table (still loading) can't be judged
        if judge and not certain:
            # a maybe, unless every possibility gives the same item
            hits += [('you', it, len(by_luck) == 1) for it in by_luck if wanted(it, mine, names)]
        elif judge and wanted(item, mine, names):
            hits.append(('you', item, True))
        elif judge and not c['snap'] and certain:
            cap = args.luck_cap if args.luck_cap is not None else luck + 40
            try:
                me = local_player(m)
                max_a, max_b = luck_accessories(m, character_id(m, me) if me else None)
            except ValueError:
                max_a, max_b = 2, 2
            if args.acc_cap:
                max_a, max_b = min(max_a, args.acc_cap[0]), min(max_b, args.acc_cap[1])
            every = luck_recipes(c, rng, mine, names, max_a, max_b, body_slot(m, local_cid(m)))
            recipes = [(it, a, b, lo, min(hi, cap)) for it, a, b, lo, hi in every if lo <= cap]
            if recipes:
                hits.append(('you', recipes[0][0], 'reach:' + fmt_recipes(recipes, names, luck, acc_a, acc_b)))
            elif every:
                hits.append(('you', every[0][0], f'far:only at LCK {min(r[3] for r in every)}+, above your cap of {cap}'))
        rows = getattr(args, 'rows', None)
        if c['type'] == 4 and c['entries']:
            # a real prediction for your boss chest; online, only once everyone's seat is known
            # (until then friends can't be predicted)
            args.judged = (not (len(session_members(m)) > 1 and seats(m)[0] is None)
                           # your character unknown: a target aimed at a character can't be checked
                           and not (not me_char and char_targets(args.wants)))
        if rows is not None and c['type'] == 4:
            rows.append({'key': 'you', 'labels': sorted(labels),
                         'settings': SETTINGS['players'].get('you', {}),
                         'player': me_gt or 'You', 'char': me_char or '?',
                         'luck': (f'{luck}' + (' (locked)' if c['snap'] else '')) if luck_known else '?',
                         'want': ', '.join(shown(w) for w in for_page(mine, names)) or 'None',
                         'targets': for_page(mine, names),
                         'manip': ('-' if not judge else
                                   'maybe (luck items unknown)' if luck_known and not acc_known and
                                   manip_cell(hits[h0:] if hits is not None else [], mine).startswith('maybe')
                                   else manip_cell(hits[h0:] if hits is not None else [], mine) or '-'),
                         # no entries at all: the game hasn't filled in the chest's table yet
                         'drop': ('chest still loading' if not c['entries'] else
                                  names.get(item, hex(item)) if certain else
                                  names.get(next(iter(by_luck)), hex(next(iter(by_luck)))) if len(by_luck) == 1 else
                                  by_luck_text(by_luck, names, not luck_known)),
                         'odds': drop_odds(c, weighted, names) if c['entries'] and certain else [],
                         'items': pool_items(c, names)})
        lines.append(f'{CHEST_TYPES.get(c["type"], c["type"])} chest @ {h:08X}  state {c["state"]}  '
                     f'rng[{c["key"]}]={rng:08X}  LCK {luck} acc {acc_a}/{acc_b} ({src})')
        if certain:
            lines.append(f'  >>> {"you, " + who + ", " if who else ""}WILL DROP: {fmt_item(item, names)}  via {how}  (roll {r}/{tot})')
        else:
            lines.append(f'  >>> {"you, " + who + ", " if who else ""}DEPENDS ON WHAT COULDN\'T BE READ: '
                         + (names.get(next(iter(by_luck)), hex(next(iter(by_luck)))) if len(by_luck) == 1
                            else by_luck_text(by_luck, names, not luck_known)))
        tw = sum(w for it, w in weighted if it and w)
        R = max(tw, 10000)
        for it, w in weighted:
            if it and w and c['count']:
                lines.append(f'      {fmt_item(it, names):<40} w={w:<5} {100 * w / R:5.1f}%')
        pool = [e[0] for e in c['entries'][c['count']:]]
        if pool:
            share = 100.0 if not c['count'] else 100 * (R - tw) / R
            lines.append(f'      + {len(pool)} pool slots share {share:.1f}%: '
                         + ', '.join(names.get(i, f'{i:X}') if i else '(empty)' for i in pool))
        if c['type'] == 4 and 2 <= c['state'] < 7:
            try:
                save_snapshot(chest_snapshot(m, h, c))
            except (ValueError, OSError):
                pass
        try:
            lines += friend_lines(m, h, c, args, names, hits if c['type'] == 4 else None)
        except ValueError:
            if c['type'] == 4:
                args.judged = False     # friends not predicted yet: don't call this load
            lines.append('  (friends not readable yet)')
    return lines


WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
WEB_FILES = {'/': ('index.html', 'text/html'), '/index.html': ('index.html', 'text/html'),
             '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
LATEST = {'state': 'waiting', 'note': 'starting', 'rows': [], 'changed': 0, 'at': time.time()}  # newest record, for the page
SETTINGS_LOCK = threading.Lock()
STOP = threading.Event()        # set by the page's Stop button; the main loop exits cleanly on it
WEB = {'error': ''}             # why the page isn't being served, '' when it is (or wasn't asked for)
PORT_WAIT_S = 10                # how long to wait for a port a replaced watcher may still hold
import secrets
TOKEN = secrets.token_hex(8)    # this watcher's id: the sidebar sends it, so another watcher on the port refuses


def serve(port):
    """Serve web/ and /state.json (the newest record) on localhost in a background thread."""
    import http.server, sys

    origins = (f'http://localhost:{port}', f'http://127.0.0.1:{port}')
    if port == 80:              # browsers leave the default port out of Origin
        origins += ('http://localhost', 'http://127.0.0.1')

    class Handler(http.server.BaseHTTPRequestHandler):
        def reply(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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
            # Only this page and the sidebar may write: exactly a JSON body (other sites can't send
            # one without a CORS preflight, which this server never answers), from this server's
            # own origin.
            ctype = self.headers.get('Content-Type', '').split(';')[0].strip().lower()
            if (self.path not in ('/set', '/quit') or ctype != 'application/json'
                    or self.headers.get('Origin', '') not in origins):
                return self.reply(403, {'ok': False, 'said': 'refused: only this page can change the watcher'})
            if self.path == '/quit':    # the page's Stop button: the main loop sees STOP and exits
                self.reply(200, {'ok': True, 'said': 'Watcher stopped.'})
                self.wfile.flush()      # answer before the main loop exits and takes this thread with it
                print('stopped from the web page', file=sys.stderr, flush=True)
                global LATEST
                with SETTINGS_LOCK:     # with show(): no frame written after this one
                    LATEST = {**LATEST, 'state': 'stopped', 'note': 'stopped from the web page', 'rows': []}
                    STOP.set()          # show() leaves LATEST alone from here on
                return
            try:
                req = json.loads(self.rfile.read(min(int(self.headers.get('Content-Length', 0)), 4096)))
                if req.get('token') not in (None, TOKEN):
                    return self.reply(409, {'ok': False, 'said': (
                        f'another watcher is serving port {port}, not the one in the sidebar. '
                        f'Stop it (its page has a Stop button), then run /chest.')})
                with SETTINGS_LOCK:
                    said = apply_setting(req, LATEST.get('rows', []))
            except (ValueError, TypeError, AttributeError) as e:
                return self.reply(400, {'ok': False, 'said': str(e)})
            except Exception as e:      # a bug: still answer, so the page and sidebar can say why
                return self.reply(500, {'ok': False, 'said': f'watcher error: {e!r}'})
            self.reply(200, {'ok': True, 'said': said})

        def log_message(self, *a):
            pass

    class Server(http.server.ThreadingHTTPServer):
        # Windows' SO_REUSEADDR lets a second process bind a port that's already listening, so
        # two watchers would share it; without it the bind fails and the wait below works.
        allow_reuse_address = sys.platform != 'win32'
        daemon_threads = True

    def run():
        # A watcher being replaced holds the port for a moment; one that keeps it is another watcher
        # serving the page. Say so after a while, and keep trying: once that one stops, this one
        # serves the page (changes carry a watcher id, so none land on the wrong one meanwhile).
        deadline = time.time() + PORT_WAIT_S
        while not STOP.is_set():
            try:
                srv = Server(('127.0.0.1', port), Handler)
                break
            except OSError as e:
                if time.time() > deadline and not WEB['error']:
                    WEB['error'] = (f'web page not started: port {port} is in use (another watcher already '
                                    f'serves http://localhost:{port}/? stop it there and this one takes over)')
                    print(f'{WEB["error"]}: {e}', file=sys.stderr, flush=True)
                time.sleep(2)
        else:
            return
        WEB['error'] = ''
        srv.serve_forever()

    threading.Thread(target=run, daemon=True).start()


def drop(m):
    """Let go of a game connection (its file mapping or process handle)."""
    try:
        if m is not None and hasattr(m, 'close'):
            m.close()
    except (OSError, ValueError, BufferError):
        pass
    return None


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
    ap.add_argument('--acc-a', type=int, choices=(0, 1, 2), help='override count of luck accessory A (0-2); default: read from equipment')
    ap.add_argument('--acc-b', type=int, choices=(0, 1, 2), default=0, help='override count of luck accessory B (with --acc-a)')
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
    try:
        friend_list(args)
    except (ValueError, IndexError):
        ap.error("--friend takes CHARACTER or CHARACTER:BYTE plus luck=N / acc=N, like 'Charlotte:0x21 luck=12'")
    SETTINGS['wants'] = [split_who(t) for w in args.want or [] for t in w.split(',')     # as /chest want
                         if squash(split_who(t)[1]).lstrip('=')]
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
    bases, rang = set(), object()    # stage seeds seen this run (= reroll count); base the bell last rang on
    last_emit, changed, last_text = 0.0, time.time(), None

    def show(state, out, rows=(), head=''):
        """Print a frame when it changed; in --json mode also a heartbeat every few seconds."""
        nonlocal last_out, last_text, last_emit, changed
        if STOP.is_set():       # stopping: keep the 'stopped' record the page and sidebar see
            return
        flat = [o.replace('\a', '') for o in out]
        if args.json or args.serve:
            key = [state, flat, list(rows), args.warn, WEB["error"]]
            if key != last_out:
                changed, last_out = time.time(), key
            elif time.time() - last_emit < 3:
                return
            head = head.replace('\a', '')
            global LATEST
            with SETTINGS_LOCK:
                if STOP.is_set():   # stopped while this frame was being built: keep the 'stopped' record
                    return
                LATEST = ({'state': state, 'note': flat[0] if state != 'live' else '',
                           'head': head.split('\n')[0], 'warn': args.warn, 'web': WEB['error'], 'token': TOKEN,
                           'changed': changed, 'at': time.time(), 'rows': list(rows)})   # swapped whole: the web thread reads it
            if args.json:
                print(json.dumps(LATEST), flush=True)
            last_emit = time.time()
        if not args.json and flat != last_text:
            print('\033[2J\033[H' + time.strftime('%H:%M:%S') + '\n' + '\n'.join(out), flush=True)
            last_text = flat

    while not STOP.is_set():
        now = time.time()
        if now - last_scan > args.rescan:
            # Attach, and reattach when the game restarts: the old process's memory goes stale.
            try:
                pid = Mem._find_pid()
            except SystemExit as e:
                if args.once:
                    raise
                m, chests = drop(m), []
                show('waiting', [f'{e}; waiting for it to start'])
                STOP.wait(max(args.interval, 2))
                continue
            if m is not None and m.pid != pid:
                m = drop(m)
            if m is None:
                # A different game (or DLC set): what was cached from the old one no longer holds.
                _luck_acc_cache.clear()
                _outfit_cache.clear()
                _recipe_cache.clear()
                _pool_cache.clear()
                try:
                    m = Mem(pid)
                except (SystemExit, OSError) as e:
                    if args.once:
                        raise
                    m = None    # just started (its memory isn't mapped yet), or not readable: say which
                    show('waiting', [f'the game is running but its memory can\'t be read yet: {e}'])
                    STOP.wait(max(args.interval, 2))
                    continue
            try:
                if hasattr(m, 'refresh'):
                    m.refresh()     # Windows: pick up memory the emulator mapped since attaching
                chests, last_scan = scan_chests(m, args.lo, args.hi), now
            except (ValueError, OSError):
                chests, last_scan = [], now
        with SETTINGS_LOCK:
            args.wants, args.luck_cap, args.acc_cap = want_list(args)
            args.own = set(SETTINGS['own'])
        hits, args.rows, args.warn, args.judged = [], [], '', False
        try:
            ensure_names(m, names)
            out = report(m, chests, args, names, hits) or ['no unopened boss chest in this stage yet']
        except Exception as e:      # memory changing under us (game closing, stage loading): reattach
            if args.once:
                raise
            if not isinstance(e, (ValueError, OSError)):
                import sys, traceback
                traceback.print_exc(file=sys.stderr)
            m, chests, last_scan = drop(m), [], 0.0
            show('waiting', [f'lost track of the game ({type(e).__name__}); reattaching'])
            STOP.wait(max(args.interval, 2))
            continue
        head = ''
        if args.wants and args.rows and not args.judged:    # a boss chest, but its roll isn't readable yet
            head = "... can't judge this load yet (the boss chest or a player is still loading); wait before reloading"
            out = [head] + out
        elif args.wants and args.rows:    # rows exist only for an unopened boss chest
            try:
                base = m.u32(m.u32(HANDLE_TABLE) + SEED_BASE)
            except ValueError:
                base = None
            if base is not None:
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
            unknown = [shown(w) for _, w in args.wants
                       if not re.fullmatch(r'0x[0-9a-f]+', squash(w).lstrip('='))     # an unnamed item's label
                       and not any(matches(w, v) for v in names.values())]
            if unknown and names:
                args.warn = f'No item is named like: {", ".join(unknown)}'
                head += f'\n    (no item name contains: {", ".join(unknown)})'
            out = [head] + out
            head = head.split('\n')[0]
        if args.once:
            if args.json:
                print(json.dumps({'state': 'live' if args.rows else 'no chest', 'rows': args.rows}))
            else:
                print('\n'.join(out).replace('\a', ''))
            return
        show('live' if args.rows else 'no chest', out, args.rows, head)
        STOP.wait(args.interval)
    # Stopped from the page: tell the sidebar, then return so open files close normally.
    if args.json:
        print(json.dumps({'state': 'stopped', 'note': 'stopped from the web page', 'head': '', 'warn': '',
                          'web': '', 'changed': time.time(), 'at': time.time(), 'rows': []}), flush=True)


if __name__ == '__main__':
    main()
