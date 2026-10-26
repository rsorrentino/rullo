# Rullo

Rullo is a solo, offline indoor-cycling training app for Linux and an open
alternative to Zwift. Power from a smart trainer (or a simulated device) is
turned into speed and distance by a deterministic physics model, all driven by
one normalised telemetry stream.

This repository contains the headless core only (no renderer, no Bluetooth yet).

## Quickstart

```sh
uv sync
uv run pytest
```

Ride a session against the simulated trainer:

```sh
uv run rullo --duration 30 --grade 4         # SIM mode on a 4% climb
uv run rullo --mode erg --target 250         # ERG mode holding 250 W
uv run rullo --help
```

## License

GPL-3.0-or-later.
