#!/bin/bash
# Build only against the running kernel. No force_id, ACPI bypass or fan writes.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
kit_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
driver_version=nasdisplay-a904dd88
kernel_release=$(uname -r)
source_dir="/usr/src/it87-$driver_version"
autoload_file=/etc/modules-load.d/nas-display-it87.conf
fail() { printf '%s\n' "$*" >&2; exit 1; }
if [[ $# -gt 1 || (${1:-} != '' && ${1:-} != --check) ]]; then
    fail 'Usage: bash INSTALL.sh [--check]'
fi
printf 'Kernel: %s\nDriver: it87 %s\n' "$kernel_release" "$driver_version"
for tool in python3 make gcc dkms modprobe modinfo depmod sha256sum; do
    command -v "$tool" >/dev/null || fail "Missing $tool. Run: sudo apt install build-essential dkms kmod python3"
done
headers="/lib/modules/$kernel_release/build"
[[ -f "$headers/Makefile" && -f "$headers/include/generated/utsrelease.h" && -f "$headers/Module.symvers" ]] ||
    fail "Incomplete kernel headers: $headers. Install headers matching $kernel_release; do not use another kernel's headers."
python3 - "$headers/include/generated/utsrelease.h" "$kernel_release" <<'PY'
import re,sys
from pathlib import Path
match=re.search(r'#define UTS_RELEASE "([^"]+)"',Path(sys.argv[1]).read_text())
if not match or match[1]!=sys.argv[2]:
    raise SystemExit('Header release does not match running kernel; stopped.')
PY
(cd "$kit_dir/source" && sha256sum -c SHA256SUMS)
if command -v mokutil >/dev/null; then
    secure_state=$(mokutil --sb-state 2>&1 || true)
    printf '%s\n' "$secure_state"
    [[ "$secure_state" != *'SecureBoot enabled'* ]] || fail 'Secure Boot enabled: a trusted signing key is required; this script will not disable Secure Boot.'
fi
existing_options=$(modprobe --showconfig | awk '$1 == "options" && $2 == "it87" {print}')
[[ -z "$existing_options" ]] || fail 'Existing it87 options found in modprobe configuration. Review them before installation; no options were changed.'
existing_install=$(modprobe --showconfig | awk '$1 == "install" && $2 == "it87" {print}')
[[ -z "$existing_install" ]] || fail 'Existing it87 install override found. Review it before installation.'
existing_dkms=$(dkms status -m it87)
if [[ -n "$existing_dkms" ]] && printf '%s\n' "$existing_dkms" | awk -v ver="$driver_version" 'index($0,"it87/" ver ",") != 1 {found=1} END {exit !found}'; then
    fail 'Another it87 DKMS version is registered. Preserve/review it before installing this version.'
fi
if [[ -L "$autoload_file" || ( -e "$autoload_file" && "$(cat "$autoload_file")" != 'it87' ) ]]; then
    fail "Existing $autoload_file has different contents; preserved."
fi
if [[ -d /sys/module/it87 ]]; then
    loaded_version=$(cat /sys/module/it87/version 2>/dev/null || true)
    [[ "$loaded_version" == "$driver_version" ]] || fail 'Another it87 module is already loaded; stopped without unloading it.'
fi
if [[ ${1:-} == --check ]]; then
    printf '%s\n' 'Preflight passed. No files installed and no module loaded.'
    exit 0
fi
[[ $EUID == 0 ]] || fail 'Run: sudo bash INSTALL.sh (or use --check for preflight only).'
if [[ -e "$source_dir" ]]; then
    [[ -d "$source_dir" && ! -L "$source_dir" ]] || fail "Unexpected source path: $source_dir"
    for file in it87.c compat.h Makefile VERSION dkms.conf COPYING README; do
        cmp -s "$kit_dir/source/$file" "$source_dir/$file" || fail "Existing source differs: $source_dir/$file; preserved."
    done
else
    install -d -m 0755 "$source_dir"
    for file in it87.c compat.h Makefile VERSION dkms.conf COPYING README; do
        install -m 0644 "$kit_dir/source/$file" "$source_dir/$file"
    done
fi
if [[ -z "$existing_dkms" ]]; then
    dkms add -m it87 -v "$driver_version"
fi
dkms build -m it87 -v "$driver_version" -k "$kernel_release"
dkms install -m it87 -v "$driver_version" -k "$kernel_release"
depmod -a "$kernel_release"
[[ "$(modinfo -F version it87)" == "$driver_version" ]] || fail 'Module resolution did not select the new driver; stopped before loading.'
[[ "$(modinfo -F vermagic it87)" == "$kernel_release "* ]] || fail 'Module vermagic mismatch; stopped before loading.'
if ! modprobe it87; then
    printf '%s\n' 'Module load failed. No forced ID or ACPI bypass was applied. Relevant kernel log:' >&2
    dmesg | tail -n 120 | awk 'tolower($0) ~ /it87|it8613|resource|lockdown|verification/' >&2 || true
    exit 1
fi
found=0
for h in /sys/class/hwmon/hwmon*; do
    [[ -f "$h/name" ]] || continue
    [[ "$(cat "$h/name")" == it8613* ]] || continue
    found=1
    printf '\n=== %s (%s) ===\n' "$h" "$(cat "$h/name")"
    for file in "$h"/fan*_input "$h"/fan*_label "$h"/pwm[1-9] "$h"/pwm*_enable; do
        [[ -f "$file" ]] || continue
        printf '%s: ' "$(basename "$file")"
        cat "$file"
    done
done
[[ $found == 1 ]] || fail 'Driver loaded but no IT8613 hwmon found. Do not guess a fan channel; retain this output for diagnosis.'
printf 'it87\n' > "$autoload_file"
chmod 0644 "$autoload_file"
printf '\n%s\n' 'IT8613 interface detected; driver enabled for boot. Fan mode/duty has not been set by this script. Send the RPM/PWM output before configuring fan control.'
