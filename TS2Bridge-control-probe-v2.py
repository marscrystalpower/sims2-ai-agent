"""Read-only, build-specific control-state candidate sampler. No game calls or writes."""
import argparse
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct
import sys
import urllib.request

EXPECTED_HASH = 'ee56ec6209aac4a7796370d3ca75a825809303393a036a0ba9a242701f2ab2b6'
DEFAULT_EXE = Path('D:/Program Files (x86)/EA Games/The Sims 2 Ultimate Collection/Fun with Pets/SP9/TSBin/Sims2EP9RPC.exe')
BASE = 0x400000
CACHE_RVA = 0x10890c0
VTABLE_RVA = 0xeab328
METHODS = {0x60: (0xae9fd6, bytes.fromhex('33c03981800000000f9fc0c3')),
           0x64: (0xae9fe2, bytes.fromhex('558bec5153568bf18b06ff504c'))}

def sample(pid, exe):
    if hashlib.sha256(exe.read_bytes()).hexdigest() != EXPECTED_HASH:
        raise ValueError('Unsupported executable hash; no memory read attempted')
    k = C.WinDLL('kernel32', use_last_error=True)
    k.CloseHandle.argtypes = [W.HANDLE]; k.CloseHandle.restype = W.BOOL
    base = BASE
    if base != BASE:
        raise ValueError('Unexpected image base; this first probe requires 0x400000')
    k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    k.OpenProcess.restype = W.HANDLE
    k.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p,
                                  C.c_size_t, C.POINTER(C.c_size_t)]
    k.ReadProcessMemory.restype = W.BOOL
    # PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ only.
    handle = k.OpenProcess(0x1010, False, pid)
    if not handle:
        raise C.WinError(C.get_last_error())
    def read(address, size):
        buf = C.create_string_buffer(size); got = C.c_size_t()
        if not k.ReadProcessMemory(handle, address, buf, size, C.byref(got)):
            raise C.WinError(C.get_last_error())
        if got.value != size:
            raise ValueError('Incomplete memory read')
        return buf.raw
    def u32(address):
        return struct.unpack('<I', read(address, 4))[0]
    try:
        k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
        k.QueryFullProcessImageNameW.restype = W.BOOL
        process_path = C.create_unicode_buffer(32768); path_size = W.DWORD(len(process_path))
        if not k.QueryFullProcessImageNameW(handle, 0, process_path, C.byref(path_size)):
            raise C.WinError(C.get_last_error())
        if Path(process_path.value).resolve() != exe.resolve():
            raise ValueError('PID executable does not match --exe')
        if read(base, 2) != b'MZ':
            raise ValueError('Expected game image not mapped at supported base')
        obj = u32(base + CACHE_RVA)
        if not obj:
            raise ValueError('Game-state cache not initialized; load a household first')
        vt = u32(obj)
        if vt != base + VTABLE_RVA:
            raise ValueError('Unexpected game-state vtable; refusing candidate offsets')
        for slot, (rva, signature) in METHODS.items():
            if u32(vt + slot) != base + rva or read(base + rva, len(signature)) != signature:
                raise ValueError('Getter signature mismatch; refusing candidate offsets')
        root = u32(0x14890a0)
        if not root or u32(root) != 0x12ab0b8:
            raise ValueError('Unexpected root interface')
        if u32(0x12ab0b8 + 0x68) != 0x4a4835 or read(0x4a4835, 4) != bytes.fromhex('8b4178c3'):
            raise ValueError('Object manager accessor mismatch')
        manager = u32(root + 0x78)
        if not manager or u32(manager) != 0x121d138:
            raise ValueError('Unexpected object manager interface')
        for slot, address in ((0x10c, 0x7ce130), (0x110, 0x7c7ac0), (0x114, 0x7d2fd0)):
            if u32(0x121d138 + slot) != address:
                raise ValueError('Build/Buy restriction method mismatch')
        signature = bytes.fromhex('8b817c100000568bb18010000033d23bc60f95c28ac25ec3')
        if read(0x7d2fd0, len(signature)) != signature:
            raise ValueError('Build/Buy restriction getter signature mismatch')
        vector = read(manager + 0x107c, 12)
        begin, end, capacity = struct.unpack('<III', vector)
        if not (begin <= end <= capacity) or any(x % 4 for x in (begin, end, capacity)) or (capacity - begin) // 4 > 8192:
            raise ValueError('Invalid restriction vector bounds')
        blocker_count = (end - begin) // 4
        # Two matching narrow reads reduce, but do not eliminate, race risk.
        for _ in range(3):
            a = read(obj + 0x68, 2) + read(obj + 0x80, 8)
            b = read(obj + 0x68, 2) + read(obj + 0x80, 8)
            if a == b and u32(base + CACHE_RVA) == obj and u32(obj) == vt:
                if read(manager + 0x107c, 12) != vector or u32(root + 0x78) != manager or u32(0x14890a0) != root or u32(manager) != 0x121d138:
                    raise ValueError('Restriction vector changed during sample')
                flag68, flag69, transition, save = struct.unpack('<BBii', a)
                return {'offset68Byte': flag68, 'offset69Byte': flag69,
                        'offset80Signed': transition, 'offset84Signed': save,
                        'buildBuyBlockerCountRaw': blocker_count,
                        'buildBuyRestrictionActiveCandidate': blocker_count != 0}
        raise ValueError('Candidate fields changed during sampling; try again')
    finally:
        k.CloseHandle(handle)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pid', type=int, required=True)
    p.add_argument('--exe', type=Path, default=DEFAULT_EXE)
    p.add_argument('--label', required=True, help='Operator description, not inferred event')
    p.add_argument('--output', type=Path)
    p.add_argument('--api', default='http://127.0.0.1:8765/v1/observe')
    args = p.parse_args()
    try:
        raw = sample(args.pid, args.exe)
        result = {'probe': 'control-candidates-2', 'sampledUtc': datetime.now(timezone.utc).isoformat(),
                  'pid': args.pid, 'operatorLabel': args.label, 'raw': raw,
                  'buildModeAvailable': None, 'buyModeAvailable': None, 'saveAvailable': None,
                  'interpretation': 'Unvalidated candidate fields; not an emergency detector'}
        # API context is sampled separately; it is not an atomic game snapshot.
        try:
            with urllib.request.urlopen(args.api, timeout=3) as response:
                obs = json.load(response)
            household = obs.get('household', {})
            result['apiContext'] = {key: obs.get(key) for key in
                                    ('fresh', 'sampleAgeSeconds', 'screenCheckRecommended')}
            result['apiContext'].update({key: household.get(key) for key in
                ('sampledUtc', 'status', 'currentFamily', 'gamePaused', 'gameModeProbeRaw', 'gameHour', 'gameMinute')})
        except (OSError, ValueError) as error:
            result['apiContext'] = {'error': str(error)}
        payload = json.dumps(result, indent=2) + '\n'
        if args.output:
            # Refuse to overwrite previous evidence.
            with args.output.open('x', encoding='utf-8') as file:
                file.write(payload)
        print(payload, end='')
    except (OSError, ValueError) as error:
        print('Probe failed: ' + str(error), file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
