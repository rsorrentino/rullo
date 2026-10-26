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
