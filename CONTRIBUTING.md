# Contributing to Rullo

Thanks for helping. Rullo is a solo, offline indoor-cycling app for Linux, and
contributions of every size are welcome: bug reports, trainer compatibility
notes, routes, workouts, docs and code.

## Getting set up

```sh
git clone https://github.com/rsorrentino/rullo.git
cd rullo
uv sync
uv run rullo            # the app on http://127.0.0.1:8765/
```

Use scratch directories so you never touch your real settings and rides:

```sh
XDG_CONFIG_HOME=/tmp/rullo-dev XDG_DATA_HOME=/tmp/rullo-dev uv run rullo
```

## Before you open a pull request

CI runs exactly these, and a pull request needs all of them clean:

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy            # strict, src and tests
uv run pytest          # runs on the simulated trainer; no hardware, no network
```

- Add or update tests with your change. Hardware code is tested against fakes
  (see `tests/test_ble.py` and `tests/test_ant.py`); protocol codecs get
  byte-level tests.
- Every source and test file starts with the GPL-3.0-or-later header; copy it
  from an existing file.
- If you change `pyproject.toml`, commit the updated `uv.lock` too.
- Keep the core headless: only `devices/ble.py` imports `bleak` and only
  `devices/ant.py` imports `openant`.
- If you change the UI, check it at desktop and phone widths.

## Commits and branches

- Branch from `main` as `feat/<slug>`, `fix/<slug>`, `docs/<slug>` or `chore/<slug>`.
- Use [Conventional Commits](https://www.conventionalcommits.org/):
  `feat(devices): ...`, `fix(app): ...`, `docs: ...`.
- Add a line to `CHANGELOG.md` under *Unreleased* for user-visible changes.

## Trainer compatibility reports

Trainers differ. If you own one, a report is one of the most useful things you
can contribute: open a *Trainer compatibility* issue with the model, firmware,
radio (Bluetooth or ANT+) and what worked. Please include the app log with
`INFO` lines from `rullo.devices`.

## Routes and workouts

Bundled routes must be your own recording or synthetic. Workouts are plain
`.zwo` files in `src/rullo/data/workouts/`. Only submit content you have the
right to share under the project license.

## License

By contributing you agree that your contributions are licensed under
GPL-3.0-or-later, the project license.
