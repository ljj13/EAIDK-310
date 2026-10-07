#!/bin/bash
# eaidk-soak-probe.sh - one P10 soak sample + occasional pulse load.
#
# Runs from a systemd timer on the board (root).  Every run appends one
# JSON-ish sample line to /var/log/eaidk-soak/samples.log.  Every Nth run
# (default 4 -> hourly at a 15 min timer) adds a NON-destructive pulse:
# 2-core CPU burst, 200 MB anonymous allocation, 20 MB fsync'd write,
# and a Tailscale/network reachability probe.  Never touches the watchdog.
#
# Usage: eaidk-soak-probe.sh [PULSE_EVERY]
set -u
PULSE_EVERY="${1:-4}"
DIR=/var/log/eaidk-soak
mkdir -p "$DIR"
STATE="$DIR/run-counter"
n=$(cat "$STATE" 2>/dev/null || echo 0)
n=$((n + 1))
echo "$n" > "$STATE"

ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
temp=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo 0)
freq=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null)
load=$(cut -d' ' -f1 /proc/loadavg)
mem_avail=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
swap_used=$(awk '/SwapFree/{f=$2} /SwapTotal/{t=$2} END{print t-f}' /proc/meminfo)
disk_free=$(df -k / | awk 'NR==2{print $4}')
mmcerr=$(dmesg | grep -ci "mmc.*error" || true)
kwarn=$(journalctl -k -b 0 -p warning --no-pager 2>/dev/null | wc -l)
failed=$(systemctl --failed --no-legend --plain | wc -l)
ts_state=$(tailscale status --json 2>/dev/null | grep -o '"BackendState": "[^"]*"' | head -1 | cut -d'"' -f4)
ts_peer=$(tailscale ping -c 1 100.79.33.7 2>/dev/null | grep -c "pong" || true)

echo "$ts n=$n temp_mC=$temp freq_khz=$freq load=$load mem_avail_kib=$mem_avail swap_used_kib=$swap_used disk_free_kib=$disk_free mmc_err=$mmcerr kwarn=$kwarn failed_units=$failed ts_state=${ts_state:-?} ts_pong=$ts_peer" >> "$DIR/samples.log"

if [ $((n % PULSE_EVERY)) -ne 0 ]; then
    exit 0
fi

# ---- hourly pulse: bounded, non-destructive ------------------------------
BURST=${SOAK_BURST_SECONDS:-600}
echo "$ts pulse start" >> "$DIR/pulse.log"

( for i in $(seq 2); do
    timeout "$BURST" sh -c 'while :; do :; done' &
  done
  wait ) 2>/dev/null &
CPU=$!

python3 - <<'PY' &
import time
keep = []
end = time.time() + ${BURST:-600}
for i in range(200):
    keep.append(bytearray(1024 * 1024))
    for j in range(0, len(keep[i]), 4096):
        keep[i][j] = 1
while time.time() < end:
    time.sleep(5)
PY
MEM=$!

dd if=/dev/urandom of=/var/log/eaidk-soak/pulse.bin bs=1M count=20 2>/dev/null
sync
rm -f /var/log/eaidk-soak/pulse.bin

curl -6 -s -o /dev/null -m 10 https://dns.alidns.com/ \
    && echo "$ts pulse v6 ok" >> "$DIR/pulse.log" \
    || echo "$ts pulse v6 FAIL" >> "$DIR/pulse.log"

wait $CPU 2>/dev/null
wait $MEM 2>/dev/null
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) pulse end" >> "$DIR/pulse.log"
exit 0
