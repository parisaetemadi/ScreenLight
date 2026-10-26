#!/usr/bin/env python3
"""screenlight - follow the sun with your screen's colour temperature.

A small f.lux for macOS. Stdlib only; the display work is done by calling
CoreGraphics through ctypes, so there is nothing to install.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import plistlib
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

NAME = "screenlight"
LABEL = "com.screenlight.daemon"

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), NAME
)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
OVERRIDE_PATH = os.path.join(CONFIG_DIR, "override.json")
PLIST_PATH = os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")

# 6500K is the reference white: at that temperature screenlight is a no-op.
NEUTRAL_K = 6500
MIN_K, MAX_K = 1000, 10000

# A gamma ramp can take the screen darker than the backlight slider will, which
# is the point, but it can also take it past readable - and the command to undo
# that is one you have to be able to see to type. 15% is dim enough to be
# useful in a dark room and bright enough to still read.
MIN_BRIGHTNESS = 0.15

# Named temperatures, so nobody has to remember what 2300K looks like. The
# names and values are f.lux's, because they are the ones people already know.
PRESETS = {
    "ember": 1200,
    "candle": 1900,
    "incandescent": 2300,
    "tungsten": 2700,
    "halogen": 3400,
    "fluorescent": 4200,
    "daylight": 6500,
}

DEFAULTS = {
    "day": 6500,
    "night": 2700,
    "day_brightness": 1.0,
    "night_brightness": 0.85,
    "lat": None,
    "lon": None,
    # Solar elevation in degrees that bounds the transition. Above `high` it is
    # fully day, below `low` fully night. Driving the curve off elevation rather
    # than clock time means it degrades gracefully where the sun never sets.
    "elevation_high": 5.0,
    "elevation_low": -10.0,
}


# --------------------------------------------------------------------------
# colour temperature
# --------------------------------------------------------------------------

def _clamp(x: float, lo: float = 0.0, hi: float = 255.0) -> float:
    return lo if x < lo else hi if x > hi else x


def _blackbody(kelvin: float) -> tuple[float, float, float]:
    """Approximate the RGB of a blackbody at `kelvin`, each channel in 0..1.

    Tanner Helland's curve fit to the Planckian locus. Accurate to within a
    couple of percent over 1000-10000K, which is well inside what the eye
    notices on a whole screen.
    """
    t = kelvin / 100.0

    if t <= 66:
        r = 255.0
    else:
        r = 329.698727446 * ((t - 60) ** -0.1332047592)

    if t <= 66:
        g = 99.4708025861 * math.log(t) - 161.1195681661
    else:
        g = 288.1221695283 * ((t - 60) ** -0.0755148492)

    if t >= 66:
        b = 255.0
    elif t <= 19:
        b = 0.0
    else:
        b = 138.5177312231 * math.log(t - 10) - 305.0447927307

    return _clamp(r) / 255.0, _clamp(g) / 255.0, _clamp(b) / 255.0


_REFERENCE = _blackbody(NEUTRAL_K)


def whitepoint(kelvin: float) -> tuple[float, float, float]:
    """Per-channel gain for `kelvin`, normalised so 6500K is exactly (1, 1, 1).

    The raw curve fit lands a percent or two off neutral at 6500K, which would
    tint the screen while claiming to do nothing. Dividing through by the 6500K
    value makes "off" and "6500K" the same picture.
    """
    r, g, b = _blackbody(kelvin)
    rr, rg, rb = _REFERENCE
    r, g, b = r / rr, g / rg, b / rb
    peak = max(r, g, b)
    if peak > 1.0:  # cooler than neutral: scale down rather than clip
        r, g, b = r / peak, g / peak, b / peak
    return r, g, b


def parse_temperature(value) -> float:
    """Accept a preset name, a bare number, or something like "2700K"."""
    if isinstance(value, (int, float)):
        return float(value)
    key = str(value).strip().lower()
    if key in PRESETS:
        return float(PRESETS[key])
    try:
        return float(key[:-1] if key.endswith("k") else key)
    except ValueError:
        raise ValueError(
            f"don't know the temperature {value!r} - give kelvin "
            f"({MIN_K}-{MAX_K}), or one of: " + ", ".join(PRESETS)
        ) from None


def nearest_preset(kelvin: float, tolerance: float = 120.0) -> str | None:
    """The preset this temperature *is*, or None if it is between two.

    Naming anything by its closest preset would label 4400K "fluorescent",
    which is halfway through the evening ramp and not fluorescent at all. A
    name is only useful when it is nearly exact.
    """
    name = min(PRESETS, key=lambda key: abs(PRESETS[key] - kelvin))
    return name if abs(PRESETS[name] - kelvin) <= tolerance else None


def rgb8(kelvin: float) -> tuple[int, int, int]:
    r, g, b = whitepoint(kelvin)
    return round(r * 255), round(g * 255), round(b * 255)


# --------------------------------------------------------------------------
# where the sun is
# --------------------------------------------------------------------------

def _julian_day(dt: datetime) -> float:
    y, m = dt.year, dt.month
    d = dt.day + (dt.hour + dt.minute / 60.0 + dt.second / 3600.0) / 24.0
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + b - 1524.5


def _solar_terms(dt_utc: datetime) -> tuple[float, float]:
    """Return (declination in radians, equation of time in minutes).

    NOAA's solar position algorithm, which is good to a fraction of a minute
    and needs nothing but `math`.
    """
    t = (_julian_day(dt_utc) - 2451545.0) / 36525.0

    mean_long = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
    mean_anom = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    eccentricity = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)

    m = math.radians(mean_anom)
    centre = (
        math.sin(m) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(2 * m) * (0.019993 - 0.000101 * t)
        + math.sin(3 * m) * 0.000289
    )

    true_long = mean_long + centre
    omega = math.radians(125.04 - 1934.136 * t)
    apparent = true_long - 0.00569 - 0.00478 * math.sin(omega)

    obliquity = 23.0 + (26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0) / 60.0
    obliquity += 0.00256 * math.cos(omega)
    eps = math.radians(obliquity)

    declination = math.asin(math.sin(eps) * math.sin(math.radians(apparent)))

    y = math.tan(eps / 2.0) ** 2
    l0 = math.radians(mean_long)
    eq_time = 4.0 * math.degrees(
        y * math.sin(2 * l0)
        - 2 * eccentricity * math.sin(m)
        + 4 * eccentricity * y * math.sin(m) * math.cos(2 * l0)
        - 0.5 * y * y * math.sin(4 * l0)
        - 1.25 * eccentricity * eccentricity * math.sin(2 * m)
    )

    return declination, eq_time


def solar_elevation(dt_utc: datetime, lat: float, lon: float) -> float:
    """Elevation of the sun above the horizon, in degrees. Negative is below."""
    declination, eq_time = _solar_terms(dt_utc)

    minutes = dt_utc.hour * 60.0 + dt_utc.minute + dt_utc.second / 60.0
    true_solar = (minutes + eq_time + 4.0 * lon) % 1440.0
    hour_angle = math.radians(true_solar / 4.0 - 180.0)

    phi = math.radians(lat)
    cos_zenith = (
        math.sin(phi) * math.sin(declination)
        + math.cos(phi) * math.cos(declination) * math.cos(hour_angle)
    )
    return 90.0 - math.degrees(math.acos(max(-1.0, min(1.0, cos_zenith))))


def sun_times(day: datetime, lat: float, lon: float):
    """(sunrise, sunset) as UTC datetimes, or None where the sun doesn't cross.

    Polar summer and polar winter both legitimately have no sunrise; callers
    get None and fall back to reporting elevation alone.
    """
    noon = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    declination, eq_time = _solar_terms(noon)

    phi = math.radians(lat)
    # 90.833 degrees of zenith, not 90: half the sun's disc plus refraction.
    try:
        cos_ha = math.cos(math.radians(90.833)) / (
            math.cos(phi) * math.cos(declination)
        ) - math.tan(phi) * math.tan(declination)
    except ZeroDivisionError:
        return None
    if not -1.0 <= cos_ha <= 1.0:
        return None

    ha = math.degrees(math.acos(cos_ha))
    midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    rise = 720.0 - 4.0 * (lon + ha) - eq_time
    set_ = 720.0 - 4.0 * (lon - ha) - eq_time
    return midnight + timedelta(minutes=rise), midnight + timedelta(minutes=set_)


# --------------------------------------------------------------------------
# the schedule
# --------------------------------------------------------------------------

def scheduled(cfg: dict, dt_utc: datetime) -> tuple[float, float, float]:
    """(kelvin, brightness, progress toward night in 0..1) for a moment."""
    elev = solar_elevation(dt_utc, cfg["lat"], cfg["lon"])
    high, low = cfg["elevation_high"], cfg["elevation_low"]

    if elev >= high:
        night = 0.0
    elif elev <= low:
        night = 1.0
    else:
        night = (high - elev) / (high - low)

    kelvin = cfg["day"] + (cfg["night"] - cfg["day"]) * night
    bright = cfg["day_brightness"] + (cfg["night_brightness"] - cfg["day_brightness"]) * night
    return kelvin, bright, night


# --------------------------------------------------------------------------
# the displays
# --------------------------------------------------------------------------

class Displays:
    """CoreGraphics gamma tables, reached through ctypes.

    macOS hands gamma back to ColorSync when the process that set it exits, so
    the `run` daemon has to stay alive to hold a warm screen. That is also the
    safety net: kill it and the screen goes back to normal.
    """

    MAX = 16

    def __init__(self) -> None:
        path = (
            "/System/Library/Frameworks/ApplicationServices.framework/"
            "ApplicationServices"
        )
        try:
            self._cg = ctypes.CDLL(path)
        except OSError as exc:
            raise Unsupported(
                "could not load CoreGraphics - screenlight only runs on macOS"
            ) from exc

        self._cg.CGGetActiveDisplayList.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        self._cg.CGGetActiveDisplayList.restype = ctypes.c_int32
        self._cg.CGDisplayGammaTableCapacity.argtypes = [ctypes.c_uint32]
        self._cg.CGDisplayGammaTableCapacity.restype = ctypes.c_uint32
        self._cg.CGSetDisplayTransferByTable.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
        ]
        self._cg.CGSetDisplayTransferByTable.restype = ctypes.c_int32
        self._cg.CGDisplayRestoreColorSyncSettings.argtypes = []
        self._cg.CGDisplayRestoreColorSyncSettings.restype = None

    def ids(self) -> list[int]:
        buf = (ctypes.c_uint32 * self.MAX)()
        count = ctypes.c_uint32(0)
        err = self._cg.CGGetActiveDisplayList(self.MAX, buf, ctypes.byref(count))
        if err != 0:
            raise Unsupported(f"CGGetActiveDisplayList failed ({err})")
        return list(buf[: count.value])

    def apply(self, kelvin: float, brightness: float = 1.0) -> int:
        """Write the ramp to every active display. Returns how many took it."""
        r, g, b = whitepoint(kelvin)
        r, g, b = r * brightness, g * brightness, b * brightness

        applied = 0
        for did in self.ids():
            size = self._cg.CGDisplayGammaTableCapacity(did) or 256
            size = min(size, 1024)
            red = (ctypes.c_float * size)()
            green = (ctypes.c_float * size)()
            blue = (ctypes.c_float * size)()
            for i in range(size):
                v = i / (size - 1)
                red[i] = v * r
                green[i] = v * g
                blue[i] = v * b
            if self._cg.CGSetDisplayTransferByTable(did, size, red, green, blue) == 0:
                applied += 1
        return applied

    def restore(self) -> None:
        self._cg.CGDisplayRestoreColorSyncSettings()


class Unsupported(RuntimeError):
    pass


def display_names() -> list[str]:
    """Human names for the attached displays, best effort."""
    try:
        out = subprocess.run(
            ["system_profiler", "-json", "SPDisplaysDataType"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
        data = json.loads(out)
    except Exception:
        return []
    names = []
    for card in data.get("SPDisplaysDataType", []):
        for panel in card.get("spdisplays_ndrvs", []):
            name = panel.get("_name")
            if name:
                names.append(name)
    return names


# --------------------------------------------------------------------------
# config and override
# --------------------------------------------------------------------------

def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH) as fh:
            cfg.update(json.load(fh))
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        print(f"{NAME}: ignoring unreadable config ({exc})", file=sys.stderr)
    return cfg


def save_config(cfg: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    keep = {k: cfg[k] for k in DEFAULTS if k in cfg}
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(keep, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, CONFIG_PATH)


def load_override() -> dict | None:
    try:
        with open(OVERRIDE_PATH) as fh:
            ov = json.load(fh)
    except (OSError, ValueError):
        return None
    until = ov.get("until")
    if until is not None and time.time() >= until:
        clear_override()
        return None
    return ov


def save_override(
    kelvin: float | None = None,
    brightness: float | None = None,
    minutes: float | None = None,
) -> None:
    """Hold temperature, brightness, or just one of the two.

    Holding them separately is what lets `dim` dim without also freezing the
    temperature: the schedule keeps moving the colour while the brightness
    stays where it was put.
    """
    os.makedirs(CONFIG_DIR, exist_ok=True)
    ov = dict(load_override() or {})
    if kelvin is not None:
        ov["kelvin"] = kelvin
    if brightness is not None:
        ov["brightness"] = brightness
    if minutes:
        ov["until"] = time.time() + minutes * 60.0
    tmp = OVERRIDE_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(ov, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, OVERRIDE_PATH)


def clear_override() -> None:
    try:
        os.remove(OVERRIDE_PATH)
    except FileNotFoundError:
        pass


def target_now(cfg: dict, dt_utc: datetime | None = None):
    """What the screen should be right now: (kelvin, brightness, source)."""
    if located(cfg):
        kelvin, bright, _ = scheduled(cfg, dt_utc or datetime.now(timezone.utc))
        source = "auto"
    else:
        kelvin, bright = float(cfg["day"]), float(cfg["day_brightness"])
        source = "unlocated"

    ov = load_override()
    if ov:
        if "kelvin" in ov:
            kelvin, source = float(ov["kelvin"]), "manual"
        if "brightness" in ov:
            bright = float(ov["brightness"])
            if source != "manual":
                source = "dimmed"
    return kelvin, max(MIN_BRIGHTNESS, min(1.0, bright)), source


def located(cfg: dict) -> bool:
    return cfg.get("lat") is not None and cfg.get("lon") is not None


STATE_PATH = os.path.join(CONFIG_DIR, "state.json")


def load_state() -> dict:
    try:
        with open(STATE_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(kelvin: float, brightness: float) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"kelvin": kelvin, "brightness": brightness}, fh)
    os.replace(tmp, STATE_PATH)


# --------------------------------------------------------------------------
# drawing
# --------------------------------------------------------------------------

class Braille:
    """A dot canvas 2 wide and 4 tall per character cell.

    Braille rather than block glyphs because a temperature curve is a line,
    and blocks can only draw a bar. Four times the vertical resolution is the
    difference between seeing the shape of the evening ramp and seeing a step.
    """

    # Dots within a cell are numbered 1-8 in a famously non-obvious order.
    _BITS = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))

    def __init__(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows
        self.width, self.height = cols * 2, rows * 4
        self._cells = [[0] * cols for _ in range(rows)]

    def set(self, x: int, y: int) -> None:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return
        self._cells[y // 4][x // 2] |= self._BITS[x % 2][y % 4]

    def vline(self, x: int, y0: int, y1: int) -> None:
        for y in range(min(y0, y1), max(y0, y1) + 1):
            self.set(x, y)

    def row(self, r: int) -> str:
        return "".join(chr(0x2800 + c) for c in self._cells[r])


def _supports_colour(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return hasattr(stream, "isatty") and stream.isatty()


def _tint(text: str, kelvin: float, colour: bool) -> str:
    """Paint text in the colour the given temperature actually looks like."""
    if not colour:
        return text
    r, g, b = rgb8(kelvin)
    # Lift it off black so very warm temperatures stay readable on a dark
    # terminal; the hue is what carries the meaning, not the luminance.
    r, g, b = (round(80 + 0.69 * c) for c in (r, g, b))
    return f"\x1b[38;2;{r};{g};{b}m{text}\x1b[0m"


def _dim(text: str, colour: bool) -> str:
    return f"\x1b[2m{text}\x1b[0m" if colour else text


CHART_COLS = 58
CHART_ROWS = 5


def day_curve(cfg: dict, now_local: datetime, colour: bool) -> list[str]:
    """Render today's temperature schedule as a coloured braille curve."""
    midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    canvas = Braille(CHART_COLS, CHART_ROWS)
    dots = canvas.width

    temps = []
    for x in range(dots):
        at = midnight + timedelta(hours=24.0 * x / (dots - 1))
        kelvin, _, _ = scheduled(cfg, at.astimezone(timezone.utc))
        temps.append(kelvin)

    hi = max(cfg["day"], cfg["night"])
    lo = min(cfg["day"], cfg["night"])
    span = hi - lo or 1.0
    pad = span * 0.12
    top, bottom = hi + pad, lo - pad

    def to_y(k: float) -> int:
        frac = (top - k) / (top - bottom)
        return max(0, min(canvas.height - 1, round(frac * (canvas.height - 1))))

    prev = to_y(temps[0])
    for x, k in enumerate(temps):
        y = to_y(k)
        canvas.vline(x, prev, y)  # join adjacent samples so the line is solid
        prev = y

    # One constant for where the plot starts, so the axis, the hour labels and
    # the now-marker cannot drift apart from the curve again.
    gutter = 10
    out = []
    for r in range(CHART_ROWS):
        # Label the first, middle and last rows with the temperature there.
        if r == 0:
            label = f"{round(hi):>5}K"
        elif r == CHART_ROWS - 1:
            label = f"{round(lo):>5}K"
        else:
            label = " " * 6
        cells = canvas._cells[r]
        painted = "".join(
            _tint(chr(0x2800 + cell), temps[min(c * 2, dots - 1)], colour)
            for c, cell in enumerate(cells)
        )
        out.append(f"  {label}{_dim(' \u2524', colour)}{painted}")

    # x axis: a tick every three hours, labelled every six.
    axis = ["\u2500"] * CHART_COLS
    labels = [" "] * CHART_COLS
    def column_of(hour: float) -> int:
        return min(round((hour / 24.0) * (CHART_COLS - 1)), CHART_COLS - 1)

    for hour in range(3, 24, 3):
        axis[column_of(hour)] = "\u252c"
    # Both midnights get a label but no tick: the corner and the line's end
    # already mark them, and a tick there would read as a 25th hour.
    for hour, text in ((0, "12a"), (6, "6a"), (12, "12p"), (18, "6p"), (24, "12a")):
        col = column_of(hour)
        start = max(0, min(CHART_COLS - len(text), col - len(text) // 2))
        labels[start:start + len(text)] = list(text)
    out.append(" " * (gutter - 1) + _dim("\u2514" + "".join(axis), colour))
    out.append((" " * gutter + _dim("".join(labels).rstrip(), colour)).rstrip())

    # Where we are in the day.
    frac = (now_local - midnight).total_seconds() / 86400.0
    marker_col = max(0, min(CHART_COLS - 1, round(frac * (CHART_COLS - 1))))
    out.append(
        " " * (gutter + marker_col)
        + _tint("\u25b2", temps[min(marker_col * 2, dots - 1)], colour)
    )
    return out


# --------------------------------------------------------------------------
# daemon bookkeeping
# --------------------------------------------------------------------------

PID_PATH = os.path.join(CONFIG_DIR, "daemon.pid")


def daemon_pid() -> int | None:
    try:
        with open(PID_PATH) as fh:
            pid = int(fh.read().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)  # signal 0 asks "is it there?" without touching it
    except OSError:
        return None
    return pid


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def open_displays() -> Displays | None:
    try:
        return Displays()
    except Unsupported as exc:
        print(f"{NAME}: {exc}", file=sys.stderr)
        return None


def status_report(cfg: dict) -> dict:
    """Everything the menu bar app needs, in one shot."""
    kelvin, bright, source = target_now(cfg)
    ov = load_override()
    now = datetime.now(timezone.utc)

    report = {
        "kelvin": round(kelvin, 1),
        "brightness": round(bright, 3),
        "source": source,
        "preset": nearest_preset(kelvin),
        "located": located(cfg),
        "daemon": daemon_pid(),
        "day": cfg["day"],
        "night": cfg["night"],
        "hold_until": (ov or {}).get("until"),
        "progress": 0.0,
        "sunrise": None,
        "sunset": None,
        "elevation": None,
    }
    if located(cfg):
        report["progress"] = round(scheduled(cfg, now)[2], 3)
        report["elevation"] = round(solar_elevation(now, cfg["lat"], cfg["lon"]), 2)
        times = sun_times(now, cfg["lat"], cfg["lon"])
        if times:
            report["sunrise"] = times[0].astimezone().isoformat(timespec="minutes")
            report["sunset"] = times[1].astimezone().isoformat(timespec="minutes")
    return report


def cmd_status(args) -> int:
    cfg = load_config()
    if getattr(args, "json", False):
        print(json.dumps(status_report(cfg)))
        return 0
    colour = _supports_colour(sys.stdout) and not args.plain
    now_local = datetime.now(timezone.utc).astimezone()

    kelvin, bright, source = target_now(cfg)
    ov = load_override()

    lines = []
    if located(cfg) and not args.plain and source != "manual":
        lines += day_curve(cfg, now_local, colour)
        lines.append("")

    def row(key: str, value: str) -> str:
        return f"  {_dim(key.ljust(9), colour)}{value}"

    def how_long() -> str:
        if ov and ov.get("until"):
            left = max(0.0, (ov["until"] - time.time()) / 60.0)
            return f"for another {round(left)} min"
        return "until further notice"

    name = nearest_preset(kelvin)
    temp = _tint(f"{round(kelvin)}K", kelvin, colour) + (f" {name}" if name else "")
    if source == "manual":
        lines.append(row("now", f"{temp}  held by hand, {how_long()}"))
    elif source == "unlocated":
        lines.append(row("now", f"{temp}  no location set"))
    else:
        _, _, night = scheduled(cfg, datetime.now(timezone.utc))
        lines.append(row("now", f"{temp}  {round(night * 100)}% toward night"))

    if bright < 0.999:
        held = f", held {how_long()}" if source == "dimmed" else ""
        lines.append(row("dimming", f"{round(bright * 100)}% brightness{held}"))

    if located(cfg):
        times = sun_times(now_local.astimezone(timezone.utc), cfg["lat"], cfg["lon"])
        elev = solar_elevation(datetime.now(timezone.utc), cfg["lat"], cfg["lon"])
        if times:
            rise = times[0].astimezone().strftime("%H:%M")
            down = times[1].astimezone().strftime("%H:%M")
            lines.append(row("sun", f"up {rise}   down {down}   elevation {elev:+.1f}\u00b0"))
        else:
            lines.append(row("sun", f"never crosses the horizon today, elevation {elev:+.1f}\u00b0"))
        lines.append(row("where", f"{cfg['lat']:.2f}, {cfg['lon']:.2f}"))
    else:
        lines.append(row("where", f"unset - run `{NAME} config --locate`"))

    pid = daemon_pid()
    mode = {
        "manual": "held by hand",
        "dimmed": "following the sun, dimmed by hand",
    }.get(source, "following the sun")
    lines.append(row("mode", f"{mode} - daemon " + (f"running (pid {pid})" if pid else "not running")))
    def named(kelvin: float) -> str:
        name = nearest_preset(kelvin)
        return f"{round(kelvin)}K {name}" if name else f"{round(kelvin)}K"

    lines.append(row("range", f"{named(cfg['day'])}  ->  {named(cfg['night'])}"))

    names = display_names() if sys.platform == "darwin" else []
    if names:
        lines.append(row("screens", f"{len(names)} - " + ", ".join(names)))

    header = _tint(NAME, kelvin, colour)
    print(f"\n{header}\n")
    print("\n".join(lines))
    print()
    return 0


def _fade_to(displays: Displays, kelvin: float, bright: float, seconds: float) -> None:
    """Walk to the target rather than snapping, so the change goes unnoticed."""
    state = load_state()
    start_k = float(state.get("kelvin", NEUTRAL_K))
    start_b = float(state.get("brightness", 1.0))
    steps = max(1, int(seconds * 10))
    for i in range(1, steps + 1):
        f = i / steps
        displays.apply(start_k + (kelvin - start_k) * f, start_b + (bright - start_b) * f)
        if i < steps:
            time.sleep(seconds / steps)


def _push(args, kelvin: float | None, bright: float | None) -> int:
    """Record a hold, then move the screen to whatever that works out to."""
    save_override(kelvin=kelvin, brightness=bright, minutes=args.hold)
    kelvin, bright, _ = target_now(load_config())

    # Only one process may own a display's gamma: whichever set it last holds
    # it, and macOS hands the table back when that process exits. So when the
    # daemon is up it does the applying and this one just nudges it - applying
    # here too would set the ramp and then drop it again on exit.
    pid = daemon_pid()
    if pid:
        try:
            os.kill(pid, signal.SIGUSR1)
        except OSError as exc:
            print(f"{NAME}: could not reach the daemon ({exc})", file=sys.stderr)
            return 1
        _announce(args, kelvin, bright)
        return 0

    displays = open_displays()
    if displays is None:
        return 1
    if args.fade > 0:
        _fade_to(displays, kelvin, bright, args.fade)
    else:
        displays.apply(kelvin, bright)
    save_state(kelvin, bright)

    _announce(args, kelvin, bright)
    print(
        f"{NAME}: no daemon running, so macOS will undo this when this "
        f"process exits - run `{NAME} install` to make it stick",
        file=sys.stderr,
    )
    return 0


def _announce(args, kelvin: float, bright: float) -> None:
    name = nearest_preset(kelvin)
    label = f" ({name})" if name else ""
    note = f", for {round(args.hold)} min" if args.hold else ""
    dimmed = f", {round(bright * 100)}% brightness" if bright < 0.999 else ""
    print(f"{round(kelvin)}K{label}{dimmed}{note}")


def _brightness_from(args) -> float | None:
    pct = getattr(args, "dim", None)
    if pct is None:
        return None
    return max(MIN_BRIGHTNESS, min(1.0, pct / 100.0))


def cmd_set(args) -> int:
    try:
        wanted = parse_temperature(args.kelvin)
    except ValueError as exc:
        print(f"{NAME}: {exc}", file=sys.stderr)
        return 1
    kelvin = max(MIN_K, min(MAX_K, wanted))
    if kelvin != wanted:
        print(
            f"{NAME}: clamped to {round(kelvin)}K ({MIN_K}-{MAX_K} supported)",
            file=sys.stderr,
        )
    return _push(args, kelvin, _brightness_from(args))


def cmd_dim(args) -> int:
    """Scale the whole ramp down, which goes below the backlight's own floor.

    This is deliberately separate from `set`: it holds the brightness without
    freezing the colour, so the schedule carries on warming the screen while
    the dimming stays put.
    """
    bright = max(MIN_BRIGHTNESS, min(1.0, args.percent / 100.0))
    if args.percent / 100.0 < MIN_BRIGHTNESS:
        print(
            f"{NAME}: floored at {round(MIN_BRIGHTNESS * 100)}% - any darker and "
            f"you could not read the command to undo it",
            file=sys.stderr,
        )
    return _push(args, None, bright)


def cmd_off(args) -> int:
    # Neutral *and* undimmed: "off" has to mean the screen is untouched, or
    # there is no way back to a known-good picture.
    return _push(args, float(NEUTRAL_K), 1.0)


def cmd_auto(args) -> int:
    clear_override()
    cfg = load_config()
    if not located(cfg):
        print(f"{NAME}: no location set - run `{NAME} config --locate`", file=sys.stderr)
        return 1
    kelvin, bright, _ = target_now(cfg)
    displays = open_displays()
    if displays is None:
        return 1
    _fade_to(displays, kelvin, bright, args.fade)
    save_state(kelvin, bright)
    print(f"following the sun again - {round(kelvin)}K now")
    return 0


def cmd_run(args) -> int:
    cfg = load_config()
    if not located(cfg):
        print(f"{NAME}: no location set - run `{NAME} config --locate`", file=sys.stderr)
        return 1

    displays = open_displays()
    if displays is None:
        return 1

    if args.once:
        kelvin, bright, _ = target_now(cfg)
        displays.apply(kelvin, bright)
        save_state(kelvin, bright)
        print(f"{round(kelvin)}K")
        return 0

    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(PID_PATH, "w") as fh:
        fh.write(str(os.getpid()))

    stopping = False
    nudged = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    def nudge(_signum, _frame):
        # SIGUSR1 means "the override file changed, look again now". Without it
        # a menu click or a `set` would wait out the rest of the tick.
        nonlocal nudged
        nudged = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGUSR1, nudge)

    try:
        while not stopping:
            # Re-read config each tick so edits and `set` take effect without
            # a restart, and re-apply unconditionally: macOS drops the gamma
            # table on sleep, resolution changes and new monitors.
            nudged = False
            cfg = load_config()
            kelvin, bright, source = target_now(cfg)

            # Schedule drift between ticks is imperceptible, so apply it
            # directly; a jump means somebody just asked for something, and
            # that gets eased in so the screen does not snap.
            last = load_state()
            jump = abs(float(last.get("kelvin", kelvin)) - kelvin) > 120 or abs(
                float(last.get("brightness", bright)) - bright
            ) > 0.02
            if jump and last:
                _fade_to(displays, kelvin, bright, 0.6)
            else:
                displays.apply(kelvin, bright)
            save_state(kelvin, bright)

            if source == "auto":
                _, _, night = scheduled(cfg, datetime.now(timezone.utc))
                mid_transition = 0.001 < night < 0.999
            else:
                mid_transition = False

            interval = args.interval if not mid_transition else min(args.interval, 15)
            for _ in range(int(interval)):
                if stopping or nudged:
                    break
                time.sleep(1)
    finally:
        displays.restore()
        try:
            os.remove(PID_PATH)
        except FileNotFoundError:
            pass
    return 0


def _locate() -> tuple[float, float] | None:
    """Coarse latitude and longitude from the IP address. Opt-in only."""
    import urllib.request

    try:
        with urllib.request.urlopen("https://ipapi.co/json/", timeout=10) as resp:
            data = json.load(resp)
        return float(data["latitude"]), float(data["longitude"])
    except Exception as exc:
        print(f"{NAME}: could not look up location ({exc})", file=sys.stderr)
        return None


def cmd_config(args) -> int:
    cfg = load_config()

    if args.locate:
        print("asking ipapi.co where this machine is...")
        found = _locate()
        if found is None:
            return 1
        cfg["lat"], cfg["lon"] = found
        print(f"  {cfg['lat']:.2f}, {cfg['lon']:.2f}")

    for key in ("day", "night"):
        value = getattr(args, key)
        if value is not None:
            try:
                cfg[key] = parse_temperature(value)
            except ValueError as exc:
                print(f"{NAME}: {exc}", file=sys.stderr)
                return 1
    for key in ("lat", "lon", "day_brightness", "night_brightness"):
        value = getattr(args, key)
        if value is not None:
            cfg[key] = value

    if cfg["day"] is not None and not MIN_K <= cfg["day"] <= MAX_K:
        print(f"{NAME}: day must be {MIN_K}-{MAX_K}K", file=sys.stderr)
        return 1
    if cfg["night"] is not None and not MIN_K <= cfg["night"] <= MAX_K:
        print(f"{NAME}: night must be {MIN_K}-{MAX_K}K", file=sys.stderr)
        return 1

    save_config(cfg)
    width = max(len(k) for k in DEFAULTS)
    for key in sorted(DEFAULTS):
        print(f"  {key.ljust(width)}  {cfg[key]}")
    print(f"\n  {CONFIG_PATH}")
    return 0


def cmd_install(args) -> int:
    script = os.path.realpath(__file__)
    plist = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, script, "run"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardErrorPath": os.path.join(CONFIG_DIR, "daemon.log"),
        "StandardOutPath": os.path.join(CONFIG_DIR, "daemon.log"),
    }
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(PLIST_PATH), exist_ok=True)
    with open(PLIST_PATH, "wb") as fh:
        plistlib.dump(plist, fh)

    target = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{target}/{LABEL}"], capture_output=True)
    done = subprocess.run(
        ["launchctl", "bootstrap", target, PLIST_PATH], capture_output=True, text=True
    )
    if done.returncode != 0:
        # bootstrap is the modern verb; older systems only know load.
        done = subprocess.run(
            ["launchctl", "load", "-w", PLIST_PATH], capture_output=True, text=True
        )
    if done.returncode != 0:
        print(f"{NAME}: launchctl refused the agent: {done.stderr.strip()}", file=sys.stderr)
        return 1

    print(f"installed - {PLIST_PATH}")
    print("it starts at login and holds the screen warm while it runs")

    bindir = os.path.expanduser("~/.local/bin")
    link = os.path.join(bindir, NAME)
    os.makedirs(bindir, exist_ok=True)
    if os.path.islink(link) or os.path.exists(link):
        os.remove(link)
    os.symlink(script, link)
    print(f"linked  - {link}")
    if bindir not in os.environ.get("PATH", "").split(":"):
        print(f"\n  {bindir} is not on your PATH. Add this to ~/.zshrc:")
        print(f'    export PATH="{bindir}:$PATH"')
    return 0


def cmd_uninstall(args) -> int:
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True
    )
    subprocess.run(["launchctl", "unload", PLIST_PATH], capture_output=True)
    for path in (PLIST_PATH, os.path.expanduser(f"~/.local/bin/{NAME}")):
        try:
            os.remove(path)
            print(f"removed - {path}")
        except FileNotFoundError:
            pass
    clear_override()
    displays = open_displays()
    if displays is not None:
        displays.restore()
    print("screen restored - config left in place")
    return 0


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=NAME,
        description="Follow the sun with your screen's colour temperature.",
    )
    subs = parser.add_subparsers(dest="command")

    p = subs.add_parser("status", help="what the screen is doing and why")
    p.add_argument("--plain", action="store_true", help="no chart, no colour")
    p.add_argument("--json", action="store_true",
                   help="machine-readable, for the menu bar app")
    p.set_defaults(func=cmd_status)

    p = subs.add_parser("set", help="hold a temperature by hand")
    p.add_argument(
        "kelvin", metavar="TEMPERATURE",
        help=f"kelvin ({MIN_K}-{MAX_K}), or a name: " + ", ".join(PRESETS),
    )
    p.add_argument("--dim", type=float, default=None, metavar="PCT",
                   help=f"also scale brightness to this percent "
                        f"({round(MIN_BRIGHTNESS * 100)}-100)")
    p.add_argument("--fade", type=float, default=1.0, metavar="SEC")
    p.add_argument("--for", dest="hold", type=float, default=None, metavar="MIN",
                   help="go back to following the sun after this long")
    p.set_defaults(func=cmd_set)

    p = subs.add_parser(
        "dim", help="scale brightness below what the backlight slider allows"
    )
    p.add_argument("percent", type=float, metavar="PCT",
                   help=f"{round(MIN_BRIGHTNESS * 100)}-100; the colour keeps "
                        f"following the sun")
    p.add_argument("--fade", type=float, default=1.0, metavar="SEC")
    p.add_argument("--for", dest="hold", type=float, default=None, metavar="MIN")
    p.set_defaults(func=cmd_dim)

    p = subs.add_parser("off", help="neutral and undimmed")
    p.add_argument("--fade", type=float, default=1.0, metavar="SEC")
    p.add_argument("--for", dest="hold", type=float, default=None, metavar="MIN")
    p.set_defaults(func=cmd_off)

    p = subs.add_parser("auto", help="drop a manual hold and follow the sun")
    p.add_argument("--fade", type=float, default=1.0, metavar="SEC")
    p.set_defaults(func=cmd_auto)

    p = subs.add_parser("run", help="the daemon: apply the schedule on a loop")
    p.add_argument("--once", action="store_true", help="apply once and exit")
    p.add_argument("--interval", type=float, default=60, metavar="SEC")
    p.set_defaults(func=cmd_run)

    p = subs.add_parser("config", help="read or change the settings")
    p.add_argument("--day", metavar="TEMPERATURE",
                   help="kelvin or a preset name")
    p.add_argument("--night", metavar="TEMPERATURE",
                   help="kelvin or a preset name")
    p.add_argument("--lat", type=float)
    p.add_argument("--lon", type=float)
    p.add_argument("--day-brightness", dest="day_brightness", type=float, metavar="F")
    p.add_argument("--night-brightness", dest="night_brightness", type=float, metavar="F")
    p.add_argument("--locate", action="store_true",
                   help="look up coarse coordinates from this machine's IP")
    p.set_defaults(func=cmd_config)

    p = subs.add_parser("install", help="run at login via launchd")
    p.set_defaults(func=cmd_install)

    p = subs.add_parser("uninstall", help="remove the login agent and restore")
    p.set_defaults(func=cmd_uninstall)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
