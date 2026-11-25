"""Cheat: the next sub-weapon you use gets max mastery. Unlike the predictor, this writes to the game.

  python3 cheats/mastery.py next        # wait for your next sub-weapon hit, then max that one
  python3 cheats/mastery.py show        # each sub-weapon slot's points and bars

Mastery is hidden points per sub-weapon, earned per hit (double on hard mode, more with mastery
rings). The game keeps them in one table, a 32-bit value per slot, and draws the bars from the
limits table below: 8 full bars at 99999. `next` watches the points table, and the first slot that
goes up is the sub-weapon you just used, so it doesn't need to know which slot is which. Found in
Chapter 10 with Jonathan: the Bible's slot is 0x82936D7C, and writing 99999 there showed 8 bars.
Julius's sub-weapons are in the same table, further along (one at 0x82936E30).
"""
import os, struct, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))      # the predictor's modules, one folder up
from gmem import Mem                            # noqa: E402

# Mastery points table: one big-endian u32 per sub-weapon slot, every character in one table.
# Slots (counted from POINTS) seen so far: Jonathan 90-106, Julius 140 on, Richter 190-195, Simon
# 440-445, Getsu Fuma 507-509, Yoko 340-342, Maria 390-394 and Shanoa 240-246. Not yet seen:
# Charlotte and Alucard (likely 290-299 and 40-56, if the 50-slot spacing holds). Before it are other counters,
# and from 0x82937840 different data (flags, then stage times), so only this stretch is watched.
POINTS = 0x82936C00
SLOTS = 768             # up to 0x82937800
LIMITS = 0x821D48BC     # points for 0..8 full bars: 0, 16, 64, 256, 1024, 4096, 16384, 32768, 99999
MAX = 99999
CANCEL = threading.Event()  # set by Deactivate: a waiting max_next stops without changing anything


def points(m):
    return list(struct.unpack(f'>{SLOTS}I', m.read(POINTS, 4 * SLOTS)))


def limits(m):
    lim = struct.unpack('>9I', m.read(LIMITS, 36))
    if lim[0] != 0 or list(lim) != sorted(lim) or lim[-1] != MAX:
        raise ValueError('the bar limits table is not where it was found; this game version differs')
    return lim


def bars(p, lim):
    full = sum(1 for t in lim[1:] if p >= t)
    if full >= 8:
        return 8.0
    return full + (p - lim[full]) / (lim[full + 1] - lim[full])


def max_next(m, wait=60.0, said=print):
    """Wait up to `wait` seconds for a sub-weapon slot's points to go up, then set it to MAX.
    Returns a message."""
    limits(m)                           # refuse if this isn't the version the addresses came from
    CANCEL.clear()
    before = points(m)
    end = time.time() + wait
    while time.time() < end:
        if CANCEL.wait(0.1):
            return 'Deactivated: nothing was changed.'
        now = points(m)
        for i, (a, b) in enumerate(zip(before, now)):
            if b > a:
                m.write_u32(POINTS + 4 * i, MAX)
                return f'Maxed sub-weapon slot {i} ({b} -> {MAX} points, 8 bars).'
        before = [max(a, b) for a, b in zip(before, now)]
    return f'No sub-weapon hit in {wait:.0f} s, so nothing was changed. Activate it again and hit something.'


def cancel():
    """Stop a waiting max_next (the page's Deactivate). Returns a message."""
    CANCEL.set()
    return 'Deactivated: nothing was changed.'


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    m = Mem()
    if cmd == 'show':
        lim = limits(m)
        for i, p in enumerate(points(m)):
            if p:
                print(f'slot {i:2} ({POINTS + 4 * i:08X}): {p:6} points, {bars(p, lim):.1f} bars')
    elif cmd == 'next':
        print('Waiting for your next sub-weapon hit (60 s)...', flush=True)
        print(max_next(m))
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main()
