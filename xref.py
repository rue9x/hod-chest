"""Find code refs to a guest address via lis/addi pairs: python xref.py ADDR..."""
import struct, sys
from ppcdis import D, B

def xrefs(target):
    hi = ((target + 0x8000) >> 16) & 0xFFFF
    lo = target & 0xFFFF
    words = struct.unpack(f'>{(0x82873000 - 0x82220000) // 4}I', D[0x82220000 - B:0x82873000 - B])
    out = []
    for i, w in enumerate(words):
        op = w >> 26
        if op in (14, 24, 32, 34, 36, 48) and (w & 0xFFFF) == lo:   # addi/ori/lwz/lbz/stw/lfs
            ra = (w >> 16) & 31
            for j in range(max(0, i - 40), i):
                v = words[j]
                if v >> 26 == 15 and ((v >> 16) & 31) == 0 and ((v >> 21) & 31) == ra and (v & 0xFFFF) == hi:
                    out.append(0x82220000 + 4 * i)
                    break
    return out

if __name__ == '__main__':
    for t in sys.argv[1:]:
        print(t, [hex(a) for a in xrefs(int(t, 16))])
