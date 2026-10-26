# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Rullo is a solo, offline indoor-cycling training app for Linux (an open alternative to Zwift). This repo is the **headless core only**: no renderer and no Bluetooth yet. Python 3.12+, no runtime dependencies, managed with `uv`.

## Commands

```sh
uv sync                                   # install dev deps
uv run pytest                             # all tests
uv run pytest tests/test_physics.py::test_flat_steady_power_converges   # single test
uv run ruff check . && uv run ruff format .
uv run mypy                               # strict mode; checks src and tests
uv run rullo --duration 30 --grade 4      # ride against the simulated trainer
```

CI (`.github/workflows/ci.yml`) runs exactly these checks with `uv sync --locked`, so commit `uv.lock` changes alongside `pyproject.toml` changes.

`pytest-asyncio` runs in `asyncio_mode = "auto"`, so async tests are plain `async def test_...` with no decorator. Tests pass `sleep=` to `SimulatedDevice` so they don't wait in real time.

## Architecture

Everything flows through one normalised stream of `Telemetry` samples (`telemetry.py`): a frozen dataclass whose `t` is seconds since stream start and whose units are W, rpm, bpm and m/s. Every field except `t` is optional.

- **Devices** (`devices/`): `Device` is a `typing.Protocol` (connect / async-iterator `telemetry()` / `set_grade` / `set_target_power` / disconnect), not a base class. Real trainers and fakes implement it structurally. `Mode` (SIM / ERG / FREE) is the trainer resistance mode. `SimulatedDevice` produces seeded, reproducible power: it converges toward the target in ERG and eases off with grade in SIM.
- **Physics** (`physics.py`): `speed_from_power(power, grade_percent, v_prev, params, dt)` is a pure, deterministic step function: gravity, rolling and aero resistance with semi-implicit Euler, and the driving force capped at low speed to avoid a 1/v singularity. `RiderParams` is frozen.
- **Bus** (`bus.py`): `TelemetryBus` gives each subscriber its own unbounded `asyncio.Queue`, so `publish` is sync and never blocks. `subscribe()` registers eagerly, before iteration starts, so samples published right after the call are not lost.
- **Loop** (`loop.py`): `run_loop` connects the device, sends it the grade and optional ERG target (the loop is the only writer of resistance commands), pulls raw samples, computes `speed` via physics using `dt` taken from successive `sample.t` values, integrates distance into `SessionState`, publishes *enriched* samples (with `speed` filled in) to the bus, and always disconnects in `finally`. `run` wraps it with a stdout HUD subscriber; `__main__.py` is the `rullo` CLI on top of `run`.

Devices emit raw samples without speed. Speed is only ever computed in the loop, so consumers read it from the bus.

## Conventions

- Every source and test file starts with the GPL-3.0-or-later license header (copy it from an existing file).
- The public API is re-exported from `rullo/__init__.py` through `__all__`. Update it when adding public names.
- Ruff: line length 88, rules `E F I UP B ASYNC`.
