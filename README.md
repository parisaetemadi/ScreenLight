# screenlight

A small f.lux for macOS: your screen cools to daylight white in the morning and
warms toward tungsten as the sun goes down, on a curve that follows the actual
sun where you are rather than a clock.

One Python file, standard library only, **Python 3.9 or newer**. The display
work is done by calling CoreGraphics through `ctypes`, so there is nothing to
install and nothing to build — macOS already ships a `python3` that can run it,
and so does any conda or Homebrew Python you already have.

```
   6500K ┤⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡤⠤⠤⠤⠤⠤⠤⠤⠤⠤⠤⠤⠤⠤⠤⠤⠤⠤⠤⠤⠤⠤⠤⡄⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
         ┤⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡼⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢹⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
         ┤⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⡼⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢳⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
         ┤⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣰⠃⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢳⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
   2700K ┤⠒⠒⠒⠒⠒⠒⠒⠒⠒⠒⠒⠒⠒⠒⠒⠒⠒⠃⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠓⠒⠒⠒⠒⠒⠒⠒⠒⠒⠒⠒
         └───────┬──────┬──────┬──────┬───────┬──────┬──────┬───────
          12a          6a            12p            6p           12a
                                                      ▲

  now      4431K  54% toward night
  dimming  92% brightness
  sun      up 07:45   down 18:17   elevation -3.2°
  where    43.65, -79.38
  mode     following the sun - daemon running (pid 4411)
  range    6500K daylight  ->  2700K tungsten
```

The curve is painted in the colour each temperature actually produces, so the
evening ramp reads as a warm gradient rather than a line on a chart.

## Getting started

```sh
git clone https://github.com/parisaetemadi/screenlight
cd screenlight
./screenlight.py config --locate   # one network call, see "Location" below
./screenlight.py install           # run at login, and link into ~/.local/bin
```

`install` writes a launchd agent to
`~/Library/LaunchAgents/com.screenlight.daemon.plist` and starts it. From then
on the daemon holds the schedule and you only ever talk to it through the CLI.

## The menu bar

```sh
menubar/build.sh          # needs only the Xcode Command Line Tools
```

That builds the app, ad-hoc signs it (LaunchServices refuses an unsigned
bundle on Apple Silicon, reporting `-10825`, which names nothing to do with
the cause) and registers it as a login agent, so it appears straight away and
comes back at every login. `screenlight uninstall` removes both agents.

**The icon is tonight's moon, at the phase it actually is** — a crescent on
the right while it waxes, on the left while it wanes, a bare ring at new, a
full disc at full. f.lux's icon never changes; this one is different every
night and matches what is outside.

It replaced a gauge that filled as the screen warmed. The gauge was worse for
two reasons: at full warmth it became a solid black blob, which is the state
you see every evening, and what the screen is doing is already one click away
in the menu. What the sky is doing is not written anywhere else, and a mark
you are meant to ignore may as well be worth glancing at.

The menu holds three things and a quit:

| | |
| --- | --- |
| **Automatic** | drop any hold and go back to following the sun |
| **Warm now** | jump straight to the night target |
| **Off for an hour** | neutral, then back on schedule by itself |

The moon sits inside the ring with a gap, so even a full moon reads as a disc
within a circle rather than one solid shape.

The phase comes from the mean synodic month, which runs about a third of a day
out against published times at worst — invisible at eighteen pixels, and it
needs no ephemeris. The lit region is a semicircular limb closed by a
half-elliptical terminator whose width is `cos(2π × phase)`: at new that
equals the radius and the lit area vanishes, at full it is the radius the
other way and the disc fills.

There is no Swift compiler in the environment this was written in, so that
geometry is checked from the Python side instead — the same rule restated, its
area measured by sampling, and compared against the illumination formula. A
sign error would draw a crescent as a gibbous, or put it on the wrong limb,
and the tests would catch both.

It is a template image, so macOS inverts it for the dark menu bar on its own,
and it is drawn in code rather than shipped as a file — there is no asset to
keep in step with it.

**The app holds no logic of its own.** Only one process may own a display's
gamma table, and the daemon owns it; the app reads `screenlight status --json`
and asks for changes through the same CLI you would type. A second copy of the
schedule living in the app is exactly the thing that would drift out of step
with the first.

## Using it

| | |
| --- | --- |
| `screenlight status` | the chart above — what the screen is doing and why |
| `screenlight set candle` | hold 1900K until told otherwise |
| `screenlight set 2700 --for 90` | hold it for ninety minutes, then resume the schedule |
| `screenlight dim 50` | half brightness, colour still following the sun |
| `screenlight set ember --dim 40` | about as gentle as a screen gets |
| `screenlight off` | neutral and undimmed |
| `screenlight off --for 20` | twenty minutes of true colour, for editing a photo |
| `screenlight auto` | drop every hold and follow the sun again |
| `screenlight config --night candle` | change the night target |
| `screenlight run` | the daemon, in the foreground |
| `screenlight uninstall` | remove the agent, restore the screen, keep the config |

Temperatures can be a number of kelvin or a name, using f.lux's names and
values because they are the ones people already know:

| | | |
| --- | --- | --- |
| `ember` | 1200K | deep orange, barely any blue left |
| `candle` | 1900K | |
| `incandescent` | 2300K | |
| `tungsten` | 2700K | roughly where Night Shift's warmest setting lands |
| `halogen` | 3400K | |
| `fluorescent` | 4200K | |
| `daylight` | 6500K | neutral; the no-op |

Every command that changes the picture fades to it over a second by default
(`--fade 0` to snap, `--fade 10` to creep). With the daemon running, the CLI
writes the request and signals the daemon rather than touching the display
itself — see the note on gamma ownership above — so the fade happens there.

### If warm alone is not helping

`dim` is the command to reach for, and it is a different lever from the
brightness keys. The gamma ramp is applied after the backlight, so scaling it
takes the screen below the darkest the hardware slider will go — which in a
dark room is usually what is actually hurting, rather than the blue light.
`screenlight dim 40` is roughly "darker than this Mac admits is possible".

It holds brightness without freezing the colour, so the schedule carries on
warming the screen while the dimming stays where you put it. `status` shows
both holds separately.

Dimming this way costs tonal resolution — you are compressing the same 256
levels per channel into a smaller range, so below about 40% you may see
banding in gradients. It is floored at 15%, because the command to undo it is
one you have to be able to see to type.

Settings live in `~/.config/screenlight/config.json`:

| key | default | |
| --- | --- | --- |
| `day` | `6500` | daytime temperature in kelvin |
| `night` | `3400` | night temperature, "halogen" |
| `day_brightness` | `1.0` | a multiplier on the whole ramp, 0.15–1.0 |
| `night_brightness` | `0.8` | below 1 dims as well as warms |
| `lat` / `lon` | unset | decimal degrees |
| `elevation_high` | `5.0` | sun this far up means full daylight |
| `elevation_low` | `-10.0` | sun this far down means full night |

## Why it works the way it does

**The schedule is driven by the sun's elevation, not by sunrise and sunset
times.** Clock-time schedules need a special case for every place the sun
misbehaves: above the Arctic Circle in June there is no sunset to key off, and
near the equator the whole transition happens in twenty minutes. Elevation has
none of those holes — it is a continuous number everywhere on earth, every day
of the year. The transition runs from `elevation_high` down to `elevation_low`,
which at mid latitudes is about ninety minutes centred a little after sunset,
and compresses or stretches on its own as the latitude and season change.
`status` still reports sunrise and sunset because those are what you actually
think in, but nothing is computed from them.

**6500K is exactly a no-op.** The blackbody curve fit everyone uses lands one
or two percent off neutral at 6500K, which would mean the "neutral" setting
quietly tinted the screen. Dividing every temperature through by the 6500K
value fixes that, so `off` and `set 6500` produce the identical picture and the
scale means what it says.

**The daemon has to stay running.** macOS hands a display's gamma table back to
ColorSync the moment the process that set it exits, which is why `set` warns you
when no daemon is up. The flip side is a good failsafe: if screenlight ever
misbehaves, killing it restores your screen immediately, and nothing is left
behind on disk that could keep a bad gamma table loaded at boot.

**It re-applies on every tick, not only when the target changes.** macOS drops
the gamma table on display sleep, resolution changes and whenever a monitor is
plugged in, and gives no notification worth relying on. Writing the same table
every sixty seconds is cheap and makes all of those self-healing. The tick
tightens to fifteen seconds mid-transition so the evening ramp stays smooth.

**Braille, not block glyphs, for the chart.** A temperature schedule is a line,
and block characters can only draw a bar. Braille cells give four times the
vertical resolution, which is the difference between seeing the shape of the
evening ramp and seeing a step.

## Location

`config --locate` makes one HTTPS request to `ipapi.co` and writes the
coordinates it returns into the config file. That is the only network call
anywhere in the program, it is never made automatically, and the daemon never
makes it at all. If you would rather not, set the coordinates yourself —
a city is close enough, since a tenth of a degree moves sunset by seconds:

```sh
screenlight config --lat 43.65 --lon -79.38
```

## Known limits

- **macOS only.** The schedule maths is portable and tested, but the display
  backend is CoreGraphics. On anything else every command fails with a clear
  message instead of pretending to work.
- **Gamma tables, not Night Shift.** Night Shift is driven by a private
  framework Apple does not publish; this uses the public gamma table API
  instead. Because gamma is applied at the end of the display pipeline, the
  two stack rather than conflict: leaving Night Shift on and running
  screenlight over it gets you warmer than either manages alone, which is
  worth knowing if Night Shift at its warmest still is not enough. Nothing
  here depends on it either way — turn it off and screenlight behaves
  identically, just from a cooler starting point.
- **Screenshots and screen recordings are unaffected,** because gamma is
  applied after compositing. Good for sharing screenshots, bad if you were
  hoping to check the effect in one.
- **Colour-managed work needs the real whitepoint.** `screenlight off --for 30`
  before grading a photo.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

Sixty-six tests, no dependencies, run against Python 3.11 and 3.13. They cover the parts with a right answer:
the colour temperature curve, the preset names, the solar and lunar position
maths, the
schedule, the chart, the config and override files, and the JSON contract the
menu bar app decodes — that last one checked against the real `.swift` source,
because a key that changes type there does not crash the app, it just quietly
decodes nil and the menu bar goes wrong. The solar tests are pinned against
published almanac times — London's midsummer sunrise comes out one minute off
the almanac, and midwinter sunset one minute off, which is the accuracy the
algorithm claims. One test exists because of a bug that reached a real Mac: a backslash inside an
f-string expression parses fine from Python 3.12 on and is a `SyntaxError`
before it, so code written on a 3.13 machine failed to load on a 3.11 one. A
parse error cannot be caught at runtime — the whole file fails before any
version check could report it — so the test scans the module for that pattern
instead, and a second test re-introduces the original line to prove the scanner
still detects it.

The CoreGraphics calls are not covered; there is no way to
assert from inside a test that a screen got warmer. Neither is the Swift
itself — only the contract between it and the CLI.
