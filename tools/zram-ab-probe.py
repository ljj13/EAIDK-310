#!/usr/bin/env python3
"""zram-ab.py - P10 zram A/B pressure probe (board-side, run as root).

Allocates a fixed anonymous working set (75% compressible pattern +
25% incompressible), holds it while the reclaim path decides, and reports
swap activity, compression ratio, CPU cost and pressure for the CURRENT
zram configuration.  The caller reconfigures zram between runs
(scripts/zram-reconfig helper) so every run sees identical memory state.

Usage: zram-ab.py LABEL ALLOC_MB
Emits one JSON object per run on stdout.
"""
import json
import os
import sys
import time


def read(path):
    with open(path) as fh:
        return fh.read()


def meminfo():
    out = {}
    for line in read("/proc/meminfo").splitlines():
        k, v = line.split(":", 1)
        out[k] = int(v.strip().split()[0])
    return out


def vmstat():
    out = {}
    for line in read("/proc/vmstat").splitlines():
        k, v = line.split()
        out[k] = int(v)
    return out


def stat_cpu():
    for line in read("/proc/stat").splitlines():
        if line.startswith("cpu "):
            return sum(int(x) for x in line.split()[1:])


def mm_stat():
    parts = read("/sys/block/zram0/mm_stat").split()
    orig, compr = int(parts[0]), int(parts[1])
    return {"data_bytes": orig, "compr_bytes": compr,
            "ratio": round(orig / compr, 2) if compr else None}


def psi_mem():
    for line in read("/proc/pressure/memory").splitlines():
        if line.startswith("some"):
            return line.split()[1:]  # avg10 avg60 avg300 total


def zram_config():
    algo = read("/sys/block/zram0/comp_algorithm").strip()
    cur = algo.split("[")[1].split("]")[0] if "[" in algo else algo.split()[0]
    size = int(read("/sys/block/zram0/disksize").strip())
    return {"algo": cur, "disksize_bytes": size}


def main():
    label = sys.argv[1]
    alloc_mb = int(sys.argv[2])
    block = b"EAIDK310-zram-probe-" + bytes(range(256)) * 64  # ~17KB semi-compressible
    mem_before = meminfo()
    vm_before = vmstat()
    cpu_before = stat_cpu()
    t0 = time.monotonic()
    keep = []
    touched = 0
    try:
        for i in range(alloc_mb):
            buf = (block * 61)[:1024 * 1024]  # ~1MiB, compressible ~3:1
            if i % 4 == 3:
                buf = os.urandom(1024 * 1024)  # incompressible quarter
            keep.append(buf)
            touched += 1
            if i % 16 == 0:
                avail = meminfo().get("MemAvailable", 0)
                if avail < 120 * 1024:
                    break
                time.sleep(0.02)
        # hold: give reclaim/swap a window, touch 1 page per buffer to keep
        # everything resident-eligible
        for rep in range(6):
            for buf in keep[::7]:
                _ = buf[:4096]
            time.sleep(2.0)
    finally:
        t1 = time.monotonic()
        mem_after = meminfo()
        vm_after = vmstat()
        cpu_after = stat_cpu()
        result = {
            "label": label,
            "config": zram_config(),
            "swappiness": read("/proc/sys/vm/swappiness").strip(),
            "alloc_touched_mb": touched,
            "wall_s": round(t1 - t0, 2),
            "cpu_ticks": cpu_after - cpu_before,
            "swap_total_kib": mem_after.get("SwapTotal"),
            "swap_free_delta_kib": (mem_before.get("SwapFree", 0)
                                    - mem_after.get("SwapFree", 0)),
            "pswpin": vm_after["pswpin"] - vm_before["pswpin"],
            "pswpout": vm_after["pswpout"] - vm_before["pswpout"],
            "mm_stat": mm_stat(),
            "mem_available_kib_after": mem_after.get("MemAvailable"),
            "psi_some": psi_mem(),
        }
        print(json.dumps(result))
        sys.stdout.flush()
        del keep
    return 0


if __name__ == "__main__":
    sys.exit(main())
