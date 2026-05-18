#!/bin/bash
# Auto-hotplug Astro A50 (Gen 4) devices to the libvirt VM "win10"
# during firmware update (when the base toggles between normal and
# bootloader USB PIDs).
#
# Normal mode:     9886:002c (Astro A50)
# Bootloader mode: 9886:002a (Polaris)
#
# Usage:
#   ./a50-vm-usb-watcher.sh [domain-name]    # foreground
#   nohup ./a50-vm-usb-watcher.sh >/dev/null 2>&1 &   # background
# Stop with: pkill -f a50-vm-usb-watcher.sh

DOMAIN="${1:-win10}"
LOG="${A50_WATCHER_LOG:-/tmp/a50-watcher.log}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[watcher] started at $(date +%H:%M:%S) for domain=$DOMAIN" >> "$LOG"

for pid in 002a 002c; do
  cat > "$SCRIPT_DIR/a50-$pid.xml" << EOF
<hostdev mode='subsystem' type='usb' managed='yes'>
  <source>
    <vendor id='0x9886'/>
    <product id='0x$pid'/>
  </source>
</hostdev>
EOF
done

while true; do
  for pid in 002c 002a; do
    if lsusb 2>/dev/null | grep -q "9886:$pid"; then
      out=$(virsh -c qemu:///system attach-device "$DOMAIN" "$SCRIPT_DIR/a50-$pid.xml" --live 2>&1)
      # Skip noise from "already attached" errors — only log real events
      if ! echo "$out" | grep -qE 'utilisé par le pilote|in use by driver'; then
        echo "[$(date +%H:%M:%S)] 9886:$pid → $out" >> "$LOG"
      fi
    fi
  done
  sleep 0.5
done
