"""Dump everything eh-fifty knows about the A50 base+headset."""
import dataclasses
from eh_fifty import Device


def show(label, value):
    if dataclasses.is_dataclass(value):
        print(f"{label}:")
        for f in dataclasses.fields(value):
            print(f"    {f.name} = {getattr(value, f.name)!r}")
    else:
        print(f"{label}: {value!r}")


def try_call(label, fn, *args):
    try:
        show(label, fn(*args))
    except Exception as e:
        print(f"{label}: ERROR {type(e).__name__}: {e}")


with Device() as d:
    try_call("headset_status", d.get_headset_status)
    try_call("battery_status", d.get_battery_status)
    try_call("active_eq_preset", d.get_active_eq_preset)
    try_call("balance", d.get_balance)
    try_call("default_balance", d.get_default_balance)
    try_call("alert_volume", d.get_alert_volume)
    try_call("noise_gate_mode", d.get_noise_gate_mode)
    try_call("mic_eq", d.get_mic_eq)

    print("\n--- EQ presets ---")
    for i in range(4):
        try_call(f"eq_preset_name[{i}]", d.get_eq_preset_name, i)

    print("\n--- Sliders ---")
    for i in range(8):
        try_call(f"slider[{i}]", d.get_slider_value, i)
