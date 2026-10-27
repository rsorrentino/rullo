# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Rullo is a solo, offline indoor-cycling training app for Linux (an open alternative to Zwift). Python 3.12+, managed with `uv`. Runtime dependencies are `bleak` (only `devices/ble.py` imports it) and `openant` (only `devices/ant.py` imports it, lazily, so the app runs without an ANT+ stick).

## Commands

```sh
uv sync                                   # install dev deps
uv run pytest                             # all tests
uv run pytest tests/test_physics.py::test_flat_steady_power_converges   # single test
uv run ruff check . && uv run ruff format .
uv run mypy                               # strict mode; checks src and tests
uv run rullo                              # the app on http://127.0.0.1:8765/ (opens a browser)
uv run rullo app --no-browser --port 8799
uv run rullo ride --route alpine-loop --duration 60   # terminal ride on the simulated trainer
uv sync --group packaging && uv run pyinstaller packaging/rullo.spec --noconfirm   # standalone build in dist/rullo/
```

Rullo must stay pure Python and run on Linux, macOS and Windows: CI runs the tests on all three (`.github/workflows/ci.yml`), and `.github/workflows/build.yml` builds and smoke-tests the PyInstaller bundles on each OS and publishes them for `v*` tags. Read and write text files with `encoding="utf-8"` (Windows defaults to cp1252). Settings and ride folders come from `config.config_dir()` and `config.data_dir()`; never hard-code `~/.config`.

CI (`.github/workflows/ci.yml`) runs exactly these checks with `uv sync --locked`, so commit `uv.lock` changes alongside `pyproject.toml` changes.

`pytest-asyncio` runs in `asyncio_mode = "auto"`, so async tests are plain `async def test_...` with no decorator. Tests pass `sleep=` to `SimulatedDevice` so they don't wait in real time. When running the app by hand, set `XDG_CONFIG_HOME` and `XDG_DATA_HOME` to scratch directories so you don't touch the real settings and ride history. `tests/conftest.py` provides `fake_api`, a scripted local HTTP server standing in for Strava and intervals.icu. `fitdecode` is a dev-only dependency used to check the FIT writer independently.

## Architecture

Everything flows through one normalised stream of `Telemetry` samples (`telemetry.py`): a frozen dataclass whose `t` is seconds since stream start and whose units are W, rpm, bpm and m/s. Every field except `t` is optional.

- **Devices** (`devices/`): `Device` is a `typing.Protocol` (connect / async-iterator `telemetry()` / `set_grade` / `set_target_power` / disconnect), not a base class. Real trainers and fakes implement it structurally. `Mode` (SIM / ERG / FREE) is the trainer resistance mode. `SimulatedDevice` produces seeded, reproducible power: it converges toward the target in ERG and eases off with grade in SIM.
- **Physics** (`physics.py`): `speed_from_power(power, grade_percent, v_prev, params, dt)` is a pure, deterministic step function: gravity, rolling and aero resistance with semi-implicit Euler, and the driving force capped at low speed to avoid a 1/v singularity. `RiderParams` is frozen.
- **Bus** (`bus.py`): `TelemetryBus` gives each subscriber its own unbounded `asyncio.Queue`, so `publish` is sync and never blocks. `subscribe()` registers eagerly, before iteration starts, so samples published right after the call are not lost.
- **Ride clock** (`loop.RideClock`): states `waiting` (until the first pedal stroke), `riding` and `paused`. `moving` is the workout position, which `skip_to` can jump forward; `ridden` is the time actually ridden, shown to the rider. The controller and `duration_s` follow `moving`; while paused the loop calls `controller.hold()` once. The app's pump records points and metrics only while riding, calls `metrics.pause()` (so the next sample counts as dt = 0 and the NP buckets restart), and passes pause intervals to the FIT writer as timer stop/start events.
- **Loop** (`loop.py`): `run_loop` connects the device, pulls raw samples, takes the grade from the `Course` at the current distance (or a constant `grade`), computes `speed` via physics using `dt` from successive `sample.t` values, integrates distance and elevation into `SessionState`, calls the `ResistanceController` every tick, publishes *enriched* samples (with `speed`) to the bus, honours `duration_s` / `stop`, and always disconnects in `finally`.
- **Resistance** (`resistance.py`): the only writer of trainer commands. SIM sends the grade times difficulty; ERG sends workout or fixed targets with a ramp limit and a cadence-floor release; writes are rate-limited. The loop sends grade and target once at start only when no controller is given (old terminal and test path).
- **Analytics** (`analytics.py`, `tcx.py`): best power per duration from the 1 s ride trace (stored as `best_powers` in each ride JSON, and backfilled in `Library.rides()` for older rides), FTP estimates (95% of 20 min; 75% of best minute for ramp tests), CTL/ATL/TSB training load from daily TSS, and TCX export from the trace. Trace rows are `[t, power, hr, cadence, speed, elevation, distance]`; older rides lack `distance`.
- **Course / workout / session** (`course.py`, `workout.py`, `session.py`): GPX parsed with stdlib XML, resampled and smoothed. A course always loops, and non-loop tracks become out-and-back. `.zwo` steps are expanded into `Step`s, with targets as FTP fractions. `SessionMetrics` computes NP (30 s rolling over 1 s buckets), IF, TSS and laps incrementally.
- **Recorder** (`recorder.py`): a hand-written FIT encoder (file_id, events, records, laps, session, activity) with the FIT CRC. The `.fit` file is written before any upload. Change field numbers only alongside `tests/test_recorder.py`, which decodes the output with `fitdecode`.
- **Devices** (`devices/ftms.py`, `devices/wahoo.py`, `devices/ble.py`): pure encode/decode for FTMS and Heart Rate (`ftms.py`), and for Cycling Power, CSC cadence and Wahoo's vendor control characteristic `a026e005` (`wahoo.py`). The Wahoo opcodes are reverse-engineered: unlock 0x20, ERG 0x42, sim mode 0x43, grade 0x46. `BleTrainer` picks FTMS or Wahoo from the GATT services at connect time, preferring FTMS, merges an optional HR strap and CSC cadence sensor, and re-applies the last grade or target after a reconnect. It takes an injectable `client_factory`, so tests drive it with a fake. To check real hardware without sending commands, wrap `bleak_client` so writes are recorded instead of sent. A trainer connected through the OS (`bluetoothctl info` shows `Connected: yes`) stops advertising, and bleak then reports "not found".
- **ANT+** (`devices/antplus.py`, `devices/ant.py`): pure 8-byte page codecs (FE-C 0x19 power and cadence, 0x31 target power, 0x33 track resistance, 0x37 user configuration; heart rate; bike power 0x10; cadence 121/122), and `AntTrainer`. `AntTrainer` runs on an injectable `Transport`; the real `OpenAntTransport` runs openant's `Node` in its own thread, so callbacks hop to the loop with `call_soon_threadsafe` and sends run via `asyncio.to_thread`. Device ids are `ant:<type>:<number>`. `RideRequest.parse` rejects mixing ANT+ and Bluetooth devices in one ride.
- **App** (`app.py`, `webserver.py`, `bridge.py`, `library.py`, `config.py`, `export/`): `App.handle` is the JSON API plus the page. One ride runs at a time as a task (`_run_ride`): a pump subscribes to the bus, feeds metrics, recorder points and `EventHub` SSE events (`idle`, `config`, `sample`, `done`), then saves `.fit` plus a JSON summary, even if the ride errors or the server is stopped. `webserver.py` is a small asyncio HTTP server that rejects non-local `Host` headers and state-changing requests without `X-Rullo: 1` (anti-CSRF), so the frontend must send that header on POSTs. Uploads use stdlib `urllib` in `asyncio.to_thread`, with base URLs injectable for tests.
- **Frontend** (`web/index.html`, `web/logo.svg` served at `/logo.svg` and `/favicon.svg`, and `web/fonts/` with bundled OFL Inter and Space Grotesk served at `/fonts/*.woff2`): the "Daylight" design. Its tokens are CSS variables on `:root`; reuse them rather than adding new colours. It is one self-contained page (no external requests; works offline) with hash routes `#ride #workouts #routes #history #settings`, the HUD and the summary. It only talks to the API and `/events`; the server replays the current ride on every reconnect.

Devices emit raw samples without speed. Speed is only ever computed in the loop, so consumers read it from the bus.

## Conventions

- Every source and test file starts with the GPL-3.0-or-later license header (copy it from an existing file).
- The public API is re-exported from `rullo/__init__.py` through `__all__`. Update it when adding public names.
- Ruff: line length 88, rules `E F I UP B ASYNC`.
