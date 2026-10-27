# Rullo - offline indoor-cycling training app.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Entry point for the standalone builds: opens the app unless told otherwise."""

import sys

from rullo.__main__ import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["app"]))
