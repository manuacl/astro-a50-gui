"""Read and write Astro Command Center EQ preset files (.astroeq).

Command Center stores one preset per file, in INI format:

    [General]
    Name=My preset

    [Eq_Bands]
    Eq_Band_1=5          (gain of bands 1-5, dB)
    Eq_Freq_Band_1=100   (centre frequency of bands 1-5, Hz)
    Eq_Bandwidth_2=2.2   (bandwidth of bands 2-4; bands 1 and 5 are shelves)

eh-fifty stores each bandwidth as the Command Center value x 4096, and its
limits are the ones Command Center uses: gain -7..7 dB, frequency
80..15000 Hz, bandwidth 0.1..3.0.
"""
import configparser

SUFFIX = ".astroeq"
GAIN_RANGE = (-7, 7)
FREQ_RANGE = (80, 15_000)
BANDWIDTH_RANGE = (0.1, 3.0)
BANDWIDTH_SCALE = 4096


def parse(text: str) -> dict:
    """Template ({"gain": [...], "bands": {1: (freq, bw), ...}}) from .astroeq text.

    Raises ValueError if the text is not a valid Command Center preset.
    """
    ini = configparser.ConfigParser(interpolation=None)
    try:
        ini.read_string(text.lstrip("\ufeff"))
    except configparser.Error as e:
        msg = f"not an Astro Command Center preset ({e.message})"
        raise ValueError(msg) from None
    if not ini.has_section("Eq_Bands"):
        msg = "not an Astro Command Center preset (no [Eq_Bands] section)"
        raise ValueError(msg)
    section = ini["Eq_Bands"]
    gain = [round(_number(section, f"Eq_Band_{b}", GAIN_RANGE)) for b in range(1, 6)]
    bands = {}
    for b in range(1, 6):
        freq = round(_number(section, f"Eq_Freq_Band_{b}", FREQ_RANGE))
        if b in (1, 5):
            bandwidth = 0
        else:
            value = _number(section, f"Eq_Bandwidth_{b}", BANDWIDTH_RANGE)
            bandwidth = round(value * BANDWIDTH_SCALE)
        bands[b] = (freq, bandwidth)
    return {"gain": gain, "bands": bands}


def dump(name: str, template: dict) -> str:
    """.astroeq text for a template, laid out like Command Center's own files."""
    bands = template["bands"]
    lines = ["[General]", f"Name={name}", "", "[Eq_Bands]"]
    lines += [f"Eq_Band_{b}={g}" for b, g in enumerate(template["gain"], start=1)]
    lines += [f"Eq_Freq_Band_{b}={bands[b][0]}" for b in range(1, 6)]
    lines += [f"Eq_Bandwidth_{b}={_format_bandwidth(bands[b][1])}" for b in (2, 3, 4)]
    return "\r\n".join(lines) + "\r\n"


def _number(section: configparser.SectionProxy, key: str, limits: tuple) -> float:
    if key not in section:
        msg = f"missing {key}"
        raise ValueError(msg)
    raw = section[key]
    try:
        value = float(raw)
    except ValueError:
        msg = f"{key} is not a number: {raw!r}"
        raise ValueError(msg) from None
    low, high = limits
    if not low <= value <= high:
        msg = f"{key}={raw} is outside {low}..{high}"
        raise ValueError(msg)
    return value


def _format_bandwidth(raw: int) -> str:
    """8192 -> "2", 9011 -> "2.2": the short decimal Command Center writes."""
    return f"{raw / BANDWIDTH_SCALE:.2f}".rstrip("0").rstrip(".")
