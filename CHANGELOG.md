# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Headless core: `Telemetry`, the `Device` protocol with SIM/ERG/FREE modes,
  `SimulatedDevice`, `TelemetryBus`, deterministic `speed_from_power` physics
  and the tick loop.
- The loop sends course grade and ERG target power to the device.
- `rullo` command (also `python -m rullo`) that rides a session against the
  simulated trainer with a console HUD.
- Injectable sleep on `SimulatedDevice` so tests run faster than real time.
- CI running ruff, mypy and pytest; pre-commit hooks.
- The app (`rullo`): pair sensors, pick routes and workouts, ride with a live
  HUD, review NP/IF/TSS and laps, browse history, and change settings.
- Bluetooth FTMS trainers (read power and cadence, set grade or target power)
  and heart-rate straps via `bleak`, with automatic reconnection.
- Wahoo KICKR trainers without FTMS (for example a KICKR SNAP on firmware 2.x):
  power from Cycling Power, with grade and ERG through Wahoo's control
  characteristic. Protocol detected on connect.
- Bluetooth cadence sensors (CSC), and the last resistance command re-applied
  after a reconnect.
- ANT+ over a USB stick (via `openant`): FE-C trainers with grade, target power
  and rider weight; power meters (read only); heart-rate straps; cadence
  sensors. The pairing screen scans either radio.
- "Daylight" visual design: light paper ground, white hairline cards, one indigo
  accent, Space Grotesk numerals and Inter text (both bundled for offline use).
  The ride screen has a power ring gauge against FTP, a workout interval
  strip, a 2x2 metric grid and the route profile.
- Per-lap NP and estimated calories in the ride summary.
- Logo and favicon, and a consistent layout on every page and screen size.
- CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, and issue and pull request templates.
- Course model with GPX import; three synthetic demo routes.
- `.zwo` workouts in ERG mode; six bundled workouts.
- Resistance controller: difficulty scaling, rate limiting, ERG ramp clamp and
  cadence-floor release.
- Session metrics (NP, IF, TSS, laps) and a dependency-free `.fit` recorder.
- Strava (OAuth) and intervals.icu uploads.
- `rullo ride` terminal rides with `--route` and `--workout`.
- Browser Ride HUD (first version, `rullo --web`, now replaced by the app): live power with zones, W/kg and %FTP, a
  course/ERG card, an elevation strip, the metric dock and a ride summary,
  streamed from the core by `rullo.bridge` over Server-Sent Events.
