"""Disassemble guest PPC code from xex_image.bin: python ppcdis.py ADDR [COUNT]"""
import os, struct, sys
from capstone import Cs, CS_ARCH_PPC, CS_MODE_32, CS_MODE_BIG_ENDIAN

B = 0x82000000
D = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'xex_image.bin'), 'rb').read()
md = Cs(CS_ARCH_PPC, CS_MODE_32 | CS_MODE_BIG_ENDIAN)


def u32(a):
    return struct.unpack('>I', D[a - B:a - B + 4])[0]


def dis(addr, count=200, stop_blr=True):
    out = []
    for a in range(addr, addr + count * 4, 4):
        ins = list(md.disasm(D[a - B:a - B + 4], a))
        txt = f'{ins[0].mnemonic} {ins[0].op_str}' if ins else f'.long 0x{u32(a):08x}'
        out.append((a, txt))
        if stop_blr and txt.strip() == 'blr':
            break
    return out


if __name__ == '__main__':
    a = int(sys.argv[1], 16)
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    for a, t in dis(a, n, len(sys.argv) < 4):
        print(f'{a:08X}  {t}')
