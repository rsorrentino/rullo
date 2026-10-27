# Rullo - offline indoor-cycling training app.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage and archive a PyInstaller build for release: `python packaging/stage.py NAME`.

Copies the platform's extras (Linux installer and desktop entry, macOS
double-click launcher) next to the built app and writes dist/NAME.tar.gz on
Linux or dist/NAME.zip elsewhere.
"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"


def main(name: str) -> Path:
    stage = DIST / name
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    shutil.copytree(DIST / "rullo", stage / "rullo")
    shutil.copy(ROOT / "LICENSE", stage / "LICENSE")
    if sys.platform.startswith("linux"):
        for extra in ("install.sh", "rullo.desktop"):
            shutil.copy(ROOT / "packaging" / "linux" / extra, stage / extra)
        shutil.copy(ROOT / "src" / "rullo" / "web" / "logo.svg", stage / "logo.svg")
        archive = shutil.make_archive(str(DIST / name), "gztar", DIST, name)
    else:
        if sys.platform == "darwin":
            shutil.copy(
                ROOT / "packaging" / "macos" / "Rullo.command", stage / "Rullo.command"
            )
        archive = shutil.make_archive(str(DIST / name), "zip", DIST, name)
    print(archive)
    return Path(archive)


if __name__ == "__main__":
    main(sys.argv[1])
