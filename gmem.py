"""Read Harmony of Despair Recomp guest (Xbox 360) memory, and write it for the cheats.

Linux: maps xenia's /dev/shm/xenia_memory_* backing file (no root or ptrace needed).
Windows: ReadProcessMemory on the game process (same user, no admin needed).
Guest address A lives at host address MEMBASE + A in the game process.

The predictor only ever reads. write() is for cheats/ alone: on Linux it writes the same backing
file, on Windows it uses WriteProcessMemory with a separate handle opened only when first needed.
"""
import os, re, struct, subprocess, sys

PROCESS_NAMES = ('harmonyrecomp', 'cvhd')
MEMBASE = 0x100000000
GUEST_SIZE = 0x100000000
XEX_BASE = 0x82000000          # the game's PE image starts here ("MZ")


class _Reads:
    def write_u32(self, a, v): self.write(a, struct.pack('>I', v))
    def write_f32(self, a, v): self.write(a, struct.pack('>f', v))
    def u8(self, a): return self.read(a, 1)[0]
    def u16(self, a): return struct.unpack('>H', self.read(a, 2))[0]
    def s16(self, a): return struct.unpack('>h', self.read(a, 2))[0]
    def u32(self, a): return struct.unpack('>I', self.read(a, 4))[0]
    def s32(self, a): return struct.unpack('>i', self.read(a, 4))[0]


# ---------------------------------------------------------------- Linux

class _LinuxMem(_Reads):
    def __init__(self, pid=None):
        import mmap
        self.pid = pid or self._find_pid()
        path, self.regions = self._maps()
        if not path:
            raise SystemExit('could not find the game memory mapping')
        self.path = path
        fd = os.open(path, os.O_RDONLY)
        try:
            self.mm = mmap.mmap(fd, 0, prot=mmap.PROT_READ)
        finally:
            os.close(fd)        # the mapping keeps its own reference

    def _maps(self):
        """(backing file, [(guest start, guest end, file offset)]) for the game's readable guest
        memory, from /proc/PID/maps; neighbours contiguous in guest and file merge into one."""
        path, regions = None, []
        with open(f'/proc/{self.pid}/maps') as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 6 or 'xenia_memory' not in parts[5] or 'r' not in parts[1]:
                    continue
                s, e = (int(x, 16) for x in parts[0].split('-'))
                if MEMBASE <= s < MEMBASE + GUEST_SIZE:
                    path = parts[5]
                    gs, ge, off = s - MEMBASE, e - MEMBASE, int(parts[2], 16)
                    last = regions[-1] if regions else None
                    if last and last[1] == gs and last[2] + (last[1] - last[0]) == off:
                        regions[-1] = (last[0], ge, last[2])
                    else:
                        regions.append((gs, ge, off))
        return path, regions

    def refresh(self):
        """Re-list readable memory: the emulator makes stage heaps readable after the watcher attaches.
        The whole backing file stays mapped, so only the list of readable ranges changes."""
        path, regions = self._maps()
        if path:
            self.regions = regions

    def close(self):
        self.mm.close()

    def write(self, addr, data):
        """Write bytes at a guest address (cheats only), through the game's memory backing file."""
        o = self.off(addr, len(data))
        with open(self.path, 'r+b') as fh:
            fh.seek(o)
            fh.write(data)

    @staticmethod
    def _find_pid():
        """The game's pid, from /proc (no pgrep needed: minimal systems don't have it)."""
        for name in PROCESS_NAMES:
            for d in sorted(os.listdir('/proc'), key=lambda x: (len(x), x)):
                if not d.isdigit():
                    continue
                try:
                    # comm is cut at 15 bytes, which can split a character: don't let that crash
                    with open(f'/proc/{d}/comm', encoding='utf-8', errors='replace') as fh:
                        if fh.read().strip() == name[:15]:     # comm holds the first 15 characters
                            return int(d)
                except OSError:
                    continue                                    # exited meanwhile, or not ours to read
        raise SystemExit('the game is not running (looked for: ' + ', '.join(PROCESS_NAMES) + ')')

    def off(self, addr, n=1):
        for s, e, o in self.regions:
            if s <= addr and addr + n <= e:
                return o + (addr - s)
        raise ValueError(f'unmapped guest addr {addr:08X}+{n:X}')

    def read(self, addr, n):
        o = self.off(addr, n)
        data = self.mm[o:o + n]
        if len(data) != n:      # past the end of the backing file
            raise ValueError(f'short read at guest addr {addr:08X}')
        return data

    def search(self, pat, lo=0, hi=0xFFFFFFFF):
        """Yield guest addresses where bytes/regex `pat` occurs."""
        rx = re.compile(re.escape(pat) if isinstance(pat, bytes) else pat, re.S)
        for s, e, o in self.regions:
            if e <= lo or s >= hi:
                continue
            a, b = max(s, lo), min(e, hi)       # only the asked-for part of the region
            for m in rx.finditer(self.mm, o + (a - s), o + (b - s)):
                yield s + (m.start() - o)


# ---------------------------------------------------------------- Windows

class _WindowsMem(_Reads):
    CHUNK = 16 << 20

    def __init__(self, pid=None):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        k.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        k.VirtualQueryEx.restype = ctypes.c_size_t
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.WriteProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        self.k = k
        self.hw = None      # write handle, opened by the first write() (cheats only)
        self.pid = pid or self._find_pid()
        self.h = k.OpenProcess(0x0400 | 0x0010, False, self.pid)   # QUERY_INFORMATION | VM_READ
        if not self.h:
            raise SystemExit(f'cannot open the game process (error {ctypes.get_last_error()})')
        self.base = MEMBASE
        if self._raw(self.base + XEX_BASE, 2) != b'MZ':
            k.CloseHandle(self.h)       # retried every few seconds while the game starts: don't leak
            raise SystemExit('game memory not found at the expected address')
        self.regions = self._regions()

    @staticmethod
    def _find_pid():
        try:
            # tasklist writes in the console's code page: decode leniently, only the game's name matters
            out = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True,
                                 text=True, encoding='oem', errors='replace').stdout
        except OSError as e:        # no tasklist: say why, the watcher keeps waiting
            raise SystemExit(f"can't list processes to find the game: {e}")
        for line in out.splitlines():
            cols = [c.strip('"') for c in line.split('","')]
            if len(cols) > 1 and cols[0].lower().removesuffix('.exe') in PROCESS_NAMES:
                return int(cols[1].strip('"'))
        raise SystemExit('the game is not running (looked for: ' +
                         ', '.join(n + '.exe' for n in PROCESS_NAMES) + ')')

    def _raw(self, host, n):
        buf = self.ct.create_string_buffer(n)
        got = self.ct.c_size_t()
        if not self.k.ReadProcessMemory(self.h, self.ct.c_void_p(host), buf, n, self.ct.byref(got)) \
                or got.value != n:
            return None
        return buf.raw

    def _regions(self):
        """Committed, readable regions inside the guest window, as guest [start, end)."""
        ct = self.ct

        class MBI(ct.Structure):
            _fields_ = [('BaseAddress', ct.c_void_p), ('AllocationBase', ct.c_void_p),
                        ('AllocationProtect', ct.c_uint32), ('PartitionId', ct.c_uint16),
                        ('RegionSize', ct.c_size_t), ('State', ct.c_uint32),
                        ('Protect', ct.c_uint32), ('Type', ct.c_uint32)]
        out, addr, end = [], self.base, self.base + GUEST_SIZE
        mbi = MBI()
        while addr < end and self.k.VirtualQueryEx(self.h, ct.c_void_p(addr), ct.byref(mbi), ct.sizeof(mbi)):
            size = mbi.RegionSize or 0x1000
            readable = mbi.Protect & 0xEE and not mbi.Protect & 0x100     # any read access, not guard
            if mbi.State == 0x1000 and readable:                          # MEM_COMMIT
                s, e = max(addr, self.base), min(addr + size, end)
                if out and out[-1][1] == s - self.base:
                    out[-1] = (out[-1][0], e - self.base)
                else:
                    out.append((s - self.base, e - self.base))
            addr += size
        return out

    def refresh(self):
        """Re-list readable memory: the emulator commits stage heaps after the watcher attaches."""
        self.regions = self._regions()

    def close(self):
        if self.h:
            self.k.CloseHandle(self.h)
            self.h = None
        if self.hw:
            self.k.CloseHandle(self.hw)
            self.hw = None

    def write(self, addr, data):
        """Write bytes at a guest address (cheats only), with WriteProcessMemory."""
        ct = self.ct
        if not self.hw:     # VM_WRITE | VM_OPERATION; the read handle never gets write access
            self.hw = self.k.OpenProcess(0x0020 | 0x0008, False, self.pid)
            if not self.hw:
                raise ValueError(f"can't open the game for writing (error {ct.get_last_error()})")
        buf = ct.create_string_buffer(bytes(data), len(data))
        done = ct.c_size_t()
        if not self.k.WriteProcessMemory(self.hw, ct.c_void_p(self.base + addr), buf, len(data),
                                         ct.byref(done)) or done.value != len(data):
            raise ValueError(f"couldn't write guest addr {addr:08X} (error {ct.get_last_error()})")

    def read(self, addr, n):
        data = self._raw(self.base + addr, n)
        if data is None:
            raise ValueError(f'unmapped guest addr {addr:08X}')
        return data

    def search(self, pat, lo=0, hi=0xFFFFFFFF):
        rx = re.compile(re.escape(pat) if isinstance(pat, bytes) else pat, re.S)
        overlap = 64
        for s, e in self.regions:
            s, e = max(s, lo), min(e, hi)
            pos = s
            while pos < e:
                n = min(self.CHUNK, e - pos)
                data = self._raw(self.base + pos, min(n + overlap, e - pos))
                if data:
                    for m in rx.finditer(data):
                        if m.start() < n:
                            yield pos + m.start()
                pos += n


Mem = _WindowsMem if sys.platform == 'win32' else _LinuxMem
