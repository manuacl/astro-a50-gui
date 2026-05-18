# Reverse-engineering helpers

Standalone scripts used while reverse-engineering the A50 Gen 4 USB protocol.
Not required to run the GUI.

| File                    | Purpose                                                   |
|-------------------------|-----------------------------------------------------------|
| `hid-capture.sh`        | Wraps `usbmon` to record HID traffic to a text file       |
| `hid-parse.py`          | Pairs OUT/IN URBs from a capture, decodes payloads, maps opcodes onto `eh_fifty._CommandType` where possible |
| `dump.py`               | Probes a single HID opcode and pretty-prints the response |
| `try-fw-args.py`        | Brute-forces argument bytes for an opcode to find which yields a different response |
| `a50-vm-usb-watcher.sh` | udev hot-attach helper for the libvirt USB passthrough used to run Astro Command Center in a Windows VM alongside the device |

ACC traffic capture workflow: pass the A50 base through libvirt to a Windows
guest, run `hid-capture.sh` on the host (usbmon sees URBs at the xhci layer,
before QEMU forwards them), exercise a feature in ACC, then run `hid-parse.py`
on the resulting log.
