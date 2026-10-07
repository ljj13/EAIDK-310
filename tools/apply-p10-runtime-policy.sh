#!/bin/bash
# apply-p10-runtime-policy.sh - P10 unattended runtime policy (userspace only).
#
# Idempotent; run on the board as root.  Everything here is reversible by
# deleting the marked config files; nothing touches the boot chain (no
# extlinux, no kernel files), so no local-authorization token is needed.
#
# What it does (evidence: docs/P10-RUNTIME-POLICY.md):
#   1. persistent journald with hard size caps  (P2 observability)
#   2. bounded coredump policy                  (P3 eMMC endurance)
#   3. apt stops keeping downloaded .debs       (P3 eMMC endurance)
#   4. one-time reclaim of stale artifacts      (P3)
#   5. TRIM now and verify the weekly timer     (P3)
set -eu
[ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 1; }

echo "== 1. persistent journal (P2)"
mkdir -p /var/log/journal
install -d /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/99-eaidk-p10.conf <<'EOF'
# P10 runtime policy (repo: tools/apply-p10-runtime-policy.sh)
# Persistent boot history for unattended failure forensics; the journal was
# volatile before (lost on every boot).  Caps keep the 7.3G eMMC safe.
[Journal]
Storage=persistent
SystemMaxUse=64M
SystemKeepFree=128M
EOF
systemctl restart systemd-journald

echo "== 2. coredump caps (P3)"
install -d /etc/systemd/coredump.conf.d
cat > /etc/systemd/coredump.conf.d/99-eaidk-p10.conf <<'EOF'
# P10 runtime policy: bounded crash evidence.  Defaults assume big disks
# (ProcessSizeMax=1G would let one dump fill a 7.3G eMMC).
[Coredump]
Storage=external
Compress=yes
ProcessSizeMax=32M
ExternalSizeMax=64M
EOF

echo "== 3. apt cache policy (P3)"
cat > /etc/apt/apt.conf.d/99-eaidk-p10-cache <<'EOF'
// P10 runtime policy: no persistent package archive cache on eMMC.
APT::Keep-Downloaded-Packages "false";
Dir::Cache::pkgcache "";
Dir::Cache::srcpkgcache "";
EOF
apt-get clean
rm -f /var/cache/apt/pkgcache.bin /var/cache/apt/srcpkgcache.bin

echo "== 4. stale artifact reclaim (one-time, idempotent)"
rm -f /var/tmp/tailscale_*.deb
rm -rf /var/tmp/p3-deploy-6.12.111

echo "== 5. TRIM now (weekly fstrim.timer must stay enabled)"
systemctl is-enabled fstrim.timer >/dev/null || systemctl enable fstrim.timer
fstrim -av || true

echo "P10_RUNTIME_POLICY=APPLIED"
