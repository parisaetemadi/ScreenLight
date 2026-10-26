"""Tests for the parts that have a right answer.

The CoreGraphics calls aren't covered - there is no way to assert a screen got
warmer from inside a test. Everything that decides *what* to send it is here.
"""

import contextlib
import importlib.util
import io
import json
import math
import os
import re
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_spec = importlib.util.spec_from_file_location(
    "screenlight", os.path.join(ROOT, "screenlight.py")
)
sl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sl)


class Whitepoint(unittest.TestCase):
    def test_neutral_is_exactly_identity(self):
        # If 6500K tinted the screen at all, "off" and "6500K" would differ and
        # the whole scale would be built on a lie.
        self.assertEqual(sl.whitepoint(6500), (1.0, 1.0, 1.0))

    def test_warm_pulls_blue_down_hardest(self):
        r, g, b = sl.whitepoint(3400)
        self.assertEqual(r, 1.0)
        self.assertLess(b, g)
        self.assertLess(g, r)

    def test_blue_rises_with_temperature(self):
        blues = [sl.whitepoint(k)[2] for k in range(1500, 6600, 250)]
        self.assertEqual(blues, sorted(blues))

    def test_cool_never_exceeds_unity(self):
        # Gamma table entries above 1.0 are clipped by the hardware, which
        # would silently crush highlights instead of cooling the picture.
        for k in (7000, 8000, 9000, 10000):
            self.assertLessEqual(max(sl.whitepoint(k)), 1.0 + 1e-9)

    def test_whole_range_stays_in_gamut(self):
        for k in range(sl.MIN_K, sl.MAX_K + 1, 100):
            for channel in sl.whitepoint(k):
                self.assertGreaterEqual(channel, 0.0)
                self.assertLessEqual(channel, 1.0 + 1e-9)


class SolarPosition(unittest.TestCase):
    LONDON = (51.4778, -0.0015)

    def test_london_midsummer_noon(self):
        elev = sl.solar_elevation(datetime(2026, 6, 21, 12, tzinfo=UTC), *self.LONDON)
        self.assertAlmostEqual(elev, 62.0, delta=0.5)

    def test_london_midwinter_noon(self):
        elev = sl.solar_elevation(datetime(2026, 12, 21, 12, tzinfo=UTC), *self.LONDON)
        self.assertAlmostEqual(elev, 15.1, delta=0.5)

    def test_equator_equinox_noon_is_overhead(self):
        elev = sl.solar_elevation(datetime(2026, 3, 20, 12, tzinfo=UTC), 0.0, 0.0)
        self.assertGreater(elev, 87.0)

    def test_night_is_below_the_horizon(self):
        elev = sl.solar_elevation(datetime(2026, 1, 15, 1, tzinfo=UTC), *self.LONDON)
        self.assertLess(elev, 0.0)


class SunTimes(unittest.TestCase):
    LONDON = (51.4778, -0.0015)

    def test_london_midsummer(self):
        rise, down = sl.sun_times(datetime(2026, 6, 21, tzinfo=UTC), *self.LONDON)
        self.assertEqual(rise.strftime("%H:%M"), "03:42")  # almanac: 03:43
        self.assertEqual(down.strftime("%H:%M"), "20:20")  # almanac: 20:21

    def test_london_midwinter(self):
        rise, down = sl.sun_times(datetime(2026, 12, 21, tzinfo=UTC), *self.LONDON)
        self.assertEqual(rise.strftime("%H:%M"), "08:03")  # almanac: 08:04
        self.assertEqual(down.strftime("%H:%M"), "15:52")  # almanac: 15:53

    def test_midsummer_day_is_longer_than_midwinter(self):
        summer = sl.sun_times(datetime(2026, 6, 21, tzinfo=UTC), *self.LONDON)
        winter = sl.sun_times(datetime(2026, 12, 21, tzinfo=UTC), *self.LONDON)
        self.assertGreater(summer[1] - summer[0], timedelta(hours=16))
        self.assertLess(winter[1] - winter[0], timedelta(hours=8))

    def test_polar_summer_and_winter_have_no_crossing(self):
        # Svalbard: midnight sun in June, polar night in December. Both must
        # come back None rather than raising out of acos.
        self.assertIsNone(sl.sun_times(datetime(2026, 6, 21, tzinfo=UTC), 78.2, 15.6))
        self.assertIsNone(sl.sun_times(datetime(2026, 12, 21, tzinfo=UTC), 78.2, 15.6))


class Moon(unittest.TestCase):
    # Published times, for a check against reality rather than against the
    # implementation. A new moon must land near phase 0, a full near 0.5.
    EVENTS = (
        ("new", datetime(2024, 1, 11, 11, 57, tzinfo=UTC), 0.0),
        ("full", datetime(2024, 1, 25, 17, 54, tzinfo=UTC), 0.5),
        ("new", datetime(2024, 4, 8, 18, 21, tzinfo=UTC), 0.0),
        ("full", datetime(2024, 9, 18, 2, 34, tzinfo=UTC), 0.5),
        ("new", datetime(2025, 3, 29, 10, 58, tzinfo=UTC), 0.0),
        ("full", datetime(2025, 12, 4, 23, 14, tzinfo=UTC), 0.5),
    )

    def test_phases_match_published_times(self):
        for kind, when, expected in self.EVENTS:
            with self.subTest(kind=kind, when=when):
                phase = sl.moon_phase(when)
                off = min(abs(phase - expected), abs(phase - expected - 1),
                          abs(phase - expected + 1)) * sl.SYNODIC_MONTH
                # The mean synodic month ignores the orbit's eccentricity, so
                # half a day of slack is the honest tolerance. On an 18-pixel
                # glyph that is not a visible difference.
                self.assertLess(off, 0.5, f"{off:.2f} days out")

    def test_phase_stays_in_range(self):
        when = datetime(2026, 1, 1, tzinfo=UTC)
        for _ in range(400):
            phase = sl.moon_phase(when)
            self.assertGreaterEqual(phase, 0.0)
            self.assertLess(phase, 1.0)
            when += timedelta(days=1)

    def test_a_full_cycle_takes_a_synodic_month(self):
        start = datetime(2026, 3, 1, tzinfo=UTC)
        later = start + timedelta(days=sl.SYNODIC_MONTH)
        self.assertAlmostEqual(sl.moon_phase(start), sl.moon_phase(later), places=6)

    def test_illumination_at_the_quarters(self):
        self.assertAlmostEqual(sl.moon_illumination(0.0), 0.0)
        self.assertAlmostEqual(sl.moon_illumination(0.25), 0.5)
        self.assertAlmostEqual(sl.moon_illumination(0.5), 1.0)
        self.assertAlmostEqual(sl.moon_illumination(0.75), 0.5)

    def test_the_drawn_shape_has_the_right_area(self):
        """The terminator the icon draws must enclose the lit fraction.

        The menu bar app builds the lit region from a semicircular limb and a
        half-ellipse terminator whose half-width is cos(2*pi*phase). There is
        no Swift compiler here, so the same rule is restated and its area
        measured: if the sign convention were wrong, a crescent would come out
        gibbous and this would catch it.
        """
        def is_lit(x, y, phase):
            waxing = phase < 0.5
            half_width = math.cos(2 * math.pi * phase) * (1 if waxing else -1)
            edge = half_width * math.sqrt(max(0.0, 1 - y * y))
            return x >= edge if waxing else x <= edge

        steps = 180
        for eighth in range(8):
            phase = eighth / 8
            with self.subTest(phase=phase):
                inside = lit = 0
                for i in range(steps):
                    for j in range(steps):
                        x = (i + 0.5) / steps * 2 - 1
                        y = (j + 0.5) / steps * 2 - 1
                        if x * x + y * y <= 1:
                            inside += 1
                            if is_lit(x, y, phase):
                                lit += 1
                self.assertAlmostEqual(lit / inside, sl.moon_illumination(phase),
                                       places=2)

    def test_crescents_fall_on_the_correct_limb(self):
        # Waxing lights the right limb, waning the left. Getting this backwards
        # draws a moon nobody in the northern hemisphere has ever seen.
        def is_lit(x, y, phase):
            waxing = phase < 0.5
            half_width = math.cos(2 * math.pi * phase) * (1 if waxing else -1)
            edge = half_width * math.sqrt(max(0.0, 1 - y * y))
            return x >= edge if waxing else x <= edge

        self.assertTrue(is_lit(0.9, 0.0, 0.125))    # waxing crescent, right
        self.assertFalse(is_lit(-0.9, 0.0, 0.125))
        self.assertTrue(is_lit(-0.9, 0.0, 0.875))   # waning crescent, left
        self.assertFalse(is_lit(0.9, 0.0, 0.875))


class Schedule(unittest.TestCase):
    def cfg(self, **over):
        c = dict(sl.DEFAULTS)
        c.update({"lat": 51.4778, "lon": -0.0015})
        c.update(over)
        return c

    def test_high_sun_is_full_day(self):
        cfg = self.cfg()
        kelvin, _, night = sl.scheduled(cfg, datetime(2026, 6, 21, 12, tzinfo=UTC))
        self.assertEqual(kelvin, cfg["day"])
        self.assertEqual(night, 0.0)

    def test_deep_night_is_full_night(self):
        cfg = self.cfg()
        kelvin, _, night = sl.scheduled(cfg, datetime(2026, 12, 21, 1, tzinfo=UTC))
        self.assertEqual(kelvin, cfg["night"])
        self.assertEqual(night, 1.0)

    def test_transition_lands_between_the_two(self):
        cfg = self.cfg()
        found = None
        start = datetime(2026, 3, 20, 17, tzinfo=UTC)
        for minute in range(0, 240):
            _, _, night = sl.scheduled(cfg, start + timedelta(minutes=minute))
            if 0.0 < night < 1.0:
                found = night
                break
        self.assertIsNotNone(found, "no partial transition found around sunset")

    def test_curve_is_monotonic_through_sunset(self):
        cfg = self.cfg()
        start = datetime(2026, 3, 20, 16, tzinfo=UTC)
        temps = [
            sl.scheduled(cfg, start + timedelta(minutes=m))[0] for m in range(0, 300, 5)
        ]
        self.assertEqual(temps, sorted(temps, reverse=True))

    def test_brightness_tracks_the_same_curve(self):
        cfg = self.cfg(night_brightness=0.6)
        _, day_b, _ = sl.scheduled(cfg, datetime(2026, 6, 21, 12, tzinfo=UTC))
        _, night_b, _ = sl.scheduled(cfg, datetime(2026, 12, 21, 1, tzinfo=UTC))
        self.assertEqual(day_b, 1.0)
        self.assertAlmostEqual(night_b, 0.6)


class Presets(unittest.TestCase):
    def test_names_resolve(self):
        self.assertEqual(sl.parse_temperature("ember"), 1200.0)
        self.assertEqual(sl.parse_temperature("Tungsten"), 2700.0)
        self.assertEqual(sl.parse_temperature("  CANDLE "), 1900.0)

    def test_numbers_resolve(self):
        self.assertEqual(sl.parse_temperature("3400"), 3400.0)
        self.assertEqual(sl.parse_temperature("2700K"), 2700.0)
        self.assertEqual(sl.parse_temperature("2700k"), 2700.0)
        self.assertEqual(sl.parse_temperature(5000), 5000.0)

    def test_nonsense_is_rejected_with_the_options(self):
        with self.assertRaises(ValueError) as caught:
            sl.parse_temperature("warmish")
        self.assertIn("ember", str(caught.exception))

    def test_every_preset_is_in_range(self):
        for name, kelvin in sl.PRESETS.items():
            with self.subTest(name=name):
                self.assertGreaterEqual(kelvin, sl.MIN_K)
                self.assertLessEqual(kelvin, sl.MAX_K)

    def test_presets_reach_warmer_than_night_shift(self):
        # The whole point of this over Night Shift, whose warm end is about
        # 2700K: there has to be somewhere further to go.
        self.assertLess(min(sl.PRESETS.values()), 2000)

    def test_nearest_preset_names_exact_hits(self):
        self.assertEqual(sl.nearest_preset(2700), "tungsten")
        self.assertEqual(sl.nearest_preset(6500), "daylight")
        self.assertEqual(sl.nearest_preset(1250), "ember")

    def test_nearest_preset_declines_to_name_a_tweener(self):
        # 4431K is halfway through the evening ramp, not "fluorescent".
        self.assertIsNone(sl.nearest_preset(4431))
        self.assertIsNone(sl.nearest_preset(5200))

    def test_every_preset_names_itself(self):
        for name, kelvin in sl.PRESETS.items():
            with self.subTest(name=name):
                self.assertEqual(sl.nearest_preset(kelvin), name)


class BrailleCanvas(unittest.TestCase):
    def test_blank_canvas_is_blank(self):
        canvas = sl.Braille(3, 1)
        self.assertEqual(canvas.row(0), "⠀" * 3)

    def test_top_left_dot(self):
        canvas = sl.Braille(1, 1)
        canvas.set(0, 0)
        self.assertEqual(canvas.row(0), "⠁")

    def test_all_eight_dots_fill_a_cell(self):
        canvas = sl.Braille(1, 1)
        for x in range(2):
            for y in range(4):
                canvas.set(x, y)
        self.assertEqual(canvas.row(0), "⣿")

    def test_out_of_bounds_is_ignored(self):
        canvas = sl.Braille(1, 1)
        for point in ((-1, 0), (0, -1), (2, 0), (0, 4)):
            canvas.set(*point)
        self.assertEqual(canvas.row(0), "⠀")

    def test_vline_spans_cells(self):
        canvas = sl.Braille(1, 2)
        canvas.vline(0, 0, 7)
        self.assertEqual(canvas.row(0), "⡇")
        self.assertEqual(canvas.row(1), "⡇")


class Chart(unittest.TestCase):
    def test_renders_expected_shape(self):
        cfg = dict(sl.DEFAULTS)
        cfg.update({"lat": 51.4778, "lon": -0.0015})
        now = datetime(2026, 10, 26, 14, 30, tzinfo=UTC)
        lines = sl.day_curve(cfg, now, colour=False)
        self.assertEqual(len(lines), sl.CHART_ROWS + 3)
        self.assertIn("6500K", lines[0])
        self.assertIn(f"{round(sl.DEFAULTS['night'])}K", lines[sl.CHART_ROWS - 1])
        self.assertIn("12p", "".join(lines))
        self.assertIn("\u25b2", lines[-1])

    def test_axis_lines_up_with_the_plot(self):
        # The axis once sat a column left of the curve, which quietly put every
        # hour label an hour out. Pin the alignment rather than eyeball it.
        cfg = dict(sl.DEFAULTS)
        cfg.update({"lat": 51.4778, "lon": -0.0015})
        lines = sl.day_curve(cfg, datetime(2026, 10, 26, 14, 30, tzinfo=UTC), colour=False)
        plot_start = lines[0].index("\u2524") + 1
        axis = lines[sl.CHART_ROWS]
        # The corner sits directly under the y axis, one column left of the plot.
        self.assertEqual(axis.index("\u2514") + 1, plot_start)
        # And both rows are exactly CHART_COLS wide from there on.
        self.assertEqual(len(lines[0]) - plot_start, sl.CHART_COLS)
        self.assertEqual(len(axis) - (axis.index("\u2514") + 1), sl.CHART_COLS)

    def test_marker_tracks_the_hour(self):
        cfg = dict(sl.DEFAULTS)
        cfg.update({"lat": 51.4778, "lon": -0.0015})
        cols = []
        for hour in (2, 12, 22):
            lines = sl.day_curve(
                cfg, datetime(2026, 10, 26, hour, tzinfo=timezone.utc).astimezone(timezone.utc),
                colour=False,
            )
            cols.append(lines[-1].index("\u25b2"))
        self.assertEqual(cols, sorted(cols))
        self.assertLess(cols[0], cols[2])

    def test_colour_only_when_asked(self):
        cfg = dict(sl.DEFAULTS)
        cfg.update({"lat": 51.4778, "lon": -0.0015})
        now = datetime(2026, 10, 26, 14, 30, tzinfo=UTC)
        self.assertNotIn("\x1b[", "".join(sl.day_curve(cfg, now, colour=False)))
        self.assertIn("\x1b[", "".join(sl.day_curve(cfg, now, colour=True)))


class ConfigAndOverride(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self._saved = (sl.CONFIG_DIR, sl.CONFIG_PATH, sl.OVERRIDE_PATH, sl.STATE_PATH)
        sl.CONFIG_DIR = self._dir.name
        sl.CONFIG_PATH = os.path.join(self._dir.name, "config.json")
        sl.OVERRIDE_PATH = os.path.join(self._dir.name, "override.json")
        sl.STATE_PATH = os.path.join(self._dir.name, "state.json")

    def tearDown(self):
        sl.CONFIG_DIR, sl.CONFIG_PATH, sl.OVERRIDE_PATH, sl.STATE_PATH = self._saved
        self._dir.cleanup()

    def test_defaults_when_absent(self):
        self.assertEqual(sl.load_config(), sl.DEFAULTS)

    def test_roundtrip(self):
        cfg = sl.load_config()
        cfg["night"] = 2700
        cfg["lat"], cfg["lon"] = 43.65, -79.38
        sl.save_config(cfg)
        self.assertEqual(sl.load_config()["night"], 2700)
        self.assertEqual(sl.load_config()["lat"], 43.65)

    def test_unknown_keys_are_not_persisted(self):
        cfg = sl.load_config()
        cfg["nonsense"] = 1
        sl.save_config(cfg)
        with open(sl.CONFIG_PATH) as fh:
            self.assertNotIn("nonsense", json.load(fh))

    def test_corrupt_config_falls_back_to_defaults(self):
        with open(sl.CONFIG_PATH, "w") as fh:
            fh.write("{not json")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(sl.load_config()["day"], sl.DEFAULTS["day"])

    def test_override_beats_the_schedule(self):
        cfg = sl.load_config()
        cfg["lat"], cfg["lon"] = 51.4778, -0.0015
        sl.save_config(cfg)
        sl.save_override(2000, 0.8, None)
        kelvin, bright, source = sl.target_now(cfg, datetime(2026, 6, 21, 12, tzinfo=UTC))
        self.assertEqual((kelvin, bright, source), (2000.0, 0.8, "manual"))

    def test_expired_override_is_dropped(self):
        sl.save_override(2000, 1.0, None)
        with open(sl.OVERRIDE_PATH) as fh:
            ov = json.load(fh)
        ov["until"] = time.time() - 1
        with open(sl.OVERRIDE_PATH, "w") as fh:
            json.dump(ov, fh)
        self.assertIsNone(sl.load_override())
        self.assertFalse(os.path.exists(sl.OVERRIDE_PATH))

    def test_timed_override_survives_until_it_does_not(self):
        sl.save_override(2000, 1.0, 10)
        self.assertIsNotNone(sl.load_override())

    def test_unlocated_falls_back_to_day(self):
        kelvin, _, source = sl.target_now(sl.load_config())
        self.assertEqual(source, "unlocated")
        self.assertEqual(kelvin, float(sl.DEFAULTS["day"]))

    def test_dimming_alone_leaves_the_colour_on_the_schedule(self):
        cfg = sl.load_config()
        cfg["lat"], cfg["lon"] = 51.4778, -0.0015
        sl.save_config(cfg)
        noon = datetime(2026, 6, 21, 12, tzinfo=UTC)
        scheduled_k = sl.scheduled(cfg, noon)[0]

        sl.save_override(brightness=0.4)
        kelvin, bright, source = sl.target_now(cfg, noon)
        self.assertEqual(kelvin, scheduled_k)
        self.assertEqual(bright, 0.4)
        self.assertEqual(source, "dimmed")

    def test_a_hold_and_a_dim_compose(self):
        cfg = sl.load_config()
        cfg["lat"], cfg["lon"] = 51.4778, -0.0015
        sl.save_config(cfg)
        sl.save_override(brightness=0.5)
        sl.save_override(kelvin=1200)
        kelvin, bright, source = sl.target_now(cfg)
        self.assertEqual((kelvin, bright, source), (1200.0, 0.5, "manual"))

    def test_brightness_is_floored(self):
        # Nothing may leave the screen too dark to read the undo command on.
        sl.save_override(brightness=0.01)
        self.assertEqual(sl.target_now(sl.load_config())[1], sl.MIN_BRIGHTNESS)

    def test_neutral_and_undimmed_is_reachable(self):
        sl.save_override(kelvin=1200, brightness=0.3)
        sl.save_override(kelvin=float(sl.NEUTRAL_K), brightness=1.0)
        kelvin, bright, _ = sl.target_now(sl.load_config())
        self.assertEqual((kelvin, bright), (float(sl.NEUTRAL_K), 1.0))
        self.assertEqual(sl.whitepoint(kelvin), (1.0, 1.0, 1.0))

    def test_config_takes_preset_names(self):
        parser = sl.build_parser()
        args = parser.parse_args(["config", "--night", "candle"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sl.cmd_config(args), 0)
        self.assertEqual(sl.load_config()["night"], 1900.0)

    def test_config_rejects_nonsense_without_writing(self):
        parser = sl.build_parser()
        before = sl.load_config()["night"]
        args = parser.parse_args(["config", "--night", "warmish"])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(sl.cmd_config(args), 1)
        self.assertEqual(sl.load_config()["night"], before)

    def test_state_roundtrip(self):
        sl.save_state(3000, 0.7)
        self.assertEqual(sl.load_state(), {"kelvin": 3000, "brightness": 0.7})


class RunsOnOlderPython(unittest.TestCase):
    """Guards against syntax that only a recent interpreter accepts.

    This is here because of a real failure: a backslash inside an f-string
    expression parses fine from 3.12 on and is a SyntaxError before it. It
    shipped because the machine it was written on had 3.13 and the Mac that
    ran it had conda's older build. A parse error cannot be caught at runtime
    - the whole file fails to load - so it has to be caught here.
    """

    # Only the shipped module. This file is parsed by whatever interpreter
    # runs the tests, so a bad f-string here fails loudly by itself; the module
    # is the one that parses on someone else's Python, which may be older than
    # the one these tests run on. It also holds a deliberate offender below.
    SOURCES = (os.path.join(ROOT, "screenlight.py"),)

    # An f-string literal, then the {...} groups inside it. Good enough for
    # this codebase: single-line, no nested braces in an expression.
    FSTRING = re.compile(r"""(?:\b|^)[fF][rR]?(['"])(?:\\.|(?!\1).)*\1""")
    HOLE = re.compile(r"\{([^{}]*)\}")

    def test_no_backslash_inside_an_fstring_expression(self):
        for path in self.SOURCES:
            with open(path) as handle:
                for number, line in enumerate(handle, 1):
                    for literal in self.FSTRING.finditer(line):
                        for hole in self.HOLE.finditer(literal.group(0)):
                            self.assertNotIn(
                                "\\", hole.group(1),
                                f"{os.path.basename(path)}:{number} puts a "
                                f"backslash in an f-string expression, which "
                                f"is a SyntaxError before Python 3.12",
                            )

    def test_the_scanner_catches_a_known_offender(self):
        # Without this, a regex that matched nothing would pass the test above
        # forever and the guard would be decorative.
        line = 'out.append(f"  {label}{_dim(chr(92) + \'u2524\', colour)}")'
        line = line.replace("chr(92) + ", "\\")
        found = [
            hole.group(1)
            for literal in self.FSTRING.finditer(line)
            for hole in self.HOLE.finditer(literal.group(0))
            if "\\" in hole.group(1)
        ]
        self.assertTrue(found, "the scanner no longer detects the original bug")

    def test_minimum_version_is_declared(self):
        self.assertLessEqual(sl.MIN_PYTHON, (3, 9))


class MenuBarContract(unittest.TestCase):
    """The Swift app decodes `status --json` into a struct.

    A key that disappears or changes type does not crash the app - Swift just
    decodes nil and the menu bar quietly goes wrong. So the contract is pinned
    here, against the real .swift source rather than a copy of it.
    """

    SWIFT = os.path.join(ROOT, "menubar", "main.swift")

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self._saved = (sl.CONFIG_DIR, sl.CONFIG_PATH, sl.OVERRIDE_PATH, sl.STATE_PATH)
        sl.CONFIG_DIR = self._dir.name
        sl.CONFIG_PATH = os.path.join(self._dir.name, "config.json")
        sl.OVERRIDE_PATH = os.path.join(self._dir.name, "override.json")
        sl.STATE_PATH = os.path.join(self._dir.name, "state.json")
        cfg = sl.load_config()
        cfg["lat"], cfg["lon"] = 43.65, -79.38
        sl.save_config(cfg)

    def tearDown(self):
        sl.CONFIG_DIR, sl.CONFIG_PATH, sl.OVERRIDE_PATH, sl.STATE_PATH = self._saved
        self._dir.cleanup()

    def swift_fields(self):
        source = open(self.SWIFT).read()
        block = source[source.index("struct Status: Decodable"):source.index("enum CLI")]
        return re.findall(r"let (\w+): (\w+)(\??)", block)

    def test_every_field_the_app_decodes_is_published(self):
        report = sl.status_report(sl.load_config())
        kinds = {"Double": (int, float), "Int": int, "String": str, "Bool": bool}
        for name, swift_type, optional in self.swift_fields():
            with self.subTest(field=name):
                self.assertIn(name, report)
                value = report[name]
                if value is None:
                    self.assertEqual(optional, "?", f"{name} was null but is not optional in Swift")
                else:
                    self.assertIsInstance(value, kinds[swift_type])

    def test_the_struct_is_not_empty(self):
        # Guards against the regex silently matching nothing and the test
        # above passing for the wrong reason.
        self.assertGreaterEqual(len(self.swift_fields()), 8)

    def test_report_survives_every_mode(self):
        cfg = sl.load_config()
        for setup, expected in (
            (lambda: sl.clear_override(), "auto"),
            (lambda: sl.save_override(brightness=0.5), "dimmed"),
            (lambda: sl.save_override(kelvin=2000), "manual"),
        ):
            setup()
            with self.subTest(source=expected):
                report = sl.status_report(sl.load_config())
                self.assertEqual(report["source"], expected)
                self.assertIsInstance(report["progress"], float)

    def test_unlocated_still_reports(self):
        sl.clear_override()
        cfg = sl.load_config()
        cfg["lat"] = cfg["lon"] = None
        sl.save_config(cfg)
        report = sl.status_report(sl.load_config())
        self.assertEqual(report["source"], "unlocated")
        self.assertFalse(report["located"])
        self.assertIsNone(report["sunset"])

    def test_json_round_trips(self):
        text = json.dumps(sl.status_report(sl.load_config()))
        self.assertEqual(json.loads(text)["day"], sl.DEFAULTS["day"])


class Cli(unittest.TestCase):
    def test_every_subcommand_parses(self):
        parser = sl.build_parser()
        for argv in (
            ["status"], ["status", "--plain"],
            ["set", "3000"], ["set", "ember"], ["set", "2700K"],
            ["set", "3000", "--for", "60", "--dim", "50"],
            ["dim", "40"], ["dim", "40", "--for", "30"],
            ["off"], ["off", "--for", "20"], ["auto"],
            ["run", "--once"], ["run", "--interval", "30"],
            ["config"], ["config", "--night", "2700"], ["config", "--locate"],
            ["install"], ["uninstall"],
        ):
            with self.subTest(argv=argv):
                args = parser.parse_args(argv)
                self.assertTrue(callable(args.func))

    def test_bare_invocation_prints_help(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(sl.main([]), 0)
        self.assertIn("usage:", out.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
