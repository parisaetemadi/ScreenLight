"""Tests for the parts that have a right answer.

The CoreGraphics calls aren't covered - there is no way to assert a screen got
warmer from inside a test. Everything that decides *what* to send it is here.
"""

import importlib.util
import json
import os
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
        self.assertIn("3400K", lines[sl.CHART_ROWS - 1])
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

    def test_state_roundtrip(self):
        sl.save_state(3000, 0.7)
        self.assertEqual(sl.load_state(), {"kelvin": 3000, "brightness": 0.7})


class Cli(unittest.TestCase):
    def test_every_subcommand_parses(self):
        parser = sl.build_parser()
        for argv in (
            ["status"], ["status", "--plain"],
            ["set", "3000"], ["set", "3000", "--for", "60", "--brightness", "0.8"],
            ["off"], ["auto"],
            ["run", "--once"], ["run", "--interval", "30"],
            ["config"], ["config", "--night", "2700"], ["config", "--locate"],
            ["install"], ["uninstall"],
        ):
            with self.subTest(argv=argv):
                args = parser.parse_args(argv)
                self.assertTrue(callable(args.func))

    def test_bare_invocation_prints_help(self):
        self.assertEqual(sl.main([]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
