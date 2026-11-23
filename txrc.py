"""Decode HoD textres_*.txrc (algorithm from guest fn 825B3890)."""
import struct, sys


def s8(b):
    return b - 256 if b > 127 else b


def decode(path):
    return decode_bytes(open(path, 'rb').read())


def decode_bytes(d):
    nsec_off, sec_off, ent_off, data_off = struct.unpack('>4I', d[0xC:0x1C])
    mult, keys = s8(d[7]), [s8(d[4]), s8(d[5]), s8(d[6])]
    nsec = struct.unpack('>H', d[nsec_off:nsec_off + 2])[0]
    out = []
    for s in range(nsec):
        cnt, start = struct.unpack('>HH', d[sec_off + 4 * s:sec_off + 4 * s + 4])
        sec = []
        for i in range(start, start + cnt):
            w = struct.unpack('>I', d[ent_off + 4 * i:ent_off + 4 * i + 4])[0]
            n, pos = w >> 22, (w & 0x3FFFFF) + data_off
            sec.append(bytes(((mult * (p // 3) + keys[p % 3]) ^ d[p]) & 0xFF for p in range(pos, pos + n)))
        out.append(sec)
    return out


if __name__ == '__main__':
    for si, sec in enumerate(decode(sys.argv[1])):
        for i, b in enumerate(sec[:int(sys.argv[2]) if len(sys.argv) > 2 else 5]):
            print(si, i, b)
