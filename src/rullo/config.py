# Rullo - offline indoor-cycling training app.
# Copyright (C) 2026 The Rullo contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Settings and on-disk locations (XDG base directories)."""

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, ClassVar


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "rullo"


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "rullo"


@dataclass
class Settings:
    """Rider, trainer and account settings, persisted as JSON."""

    mass_kg: float = 78.0
    ftp: int = 250
    difficulty: float = 0.5  # fraction of real grade the trainer applies
    cadence_floor: int = 55
    sim_power: int = 200  # what the simulated rider holds in SIM mode
    strava_client_id: str = ""
    strava_client_secret: str = ""
    strava_access_token: str = ""
    strava_refresh_token: str = ""
    strava_expires_at: int = 0
    strava_athlete: str = ""
    intervals_athlete_id: str = "0"  # 0 = the athlete who owns the API key
    intervals_api_key: str = ""

    SECRET: ClassVar[frozenset[str]] = frozenset(
        {
            "strava_client_secret",
            "strava_access_token",
            "strava_refresh_token",
            "intervals_api_key",
        }
    )
    EDITABLE: ClassVar[frozenset[str]] = frozenset(
        {
            "mass_kg",
            "ftp",
            "difficulty",
            "cadence_floor",
            "sim_power",
            "strava_client_id",
            "strava_client_secret",
            "intervals_athlete_id",
            "intervals_api_key",
        }
    )

    @classmethod
    def load(cls, path: Path) -> "Settings":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)
        tmp.replace(path)

    def update(self, changes: dict[str, Any]) -> None:
        """Apply edits from the UI, coercing to each field's type."""
        types = {f.name: f.type for f in fields(self)}
        for key, value in changes.items():
            if key not in self.EDITABLE:
                raise ValueError(f"{key} is not editable")
            if key in self.SECRET and value == "":
                continue  # blank means "keep the stored secret"
            kind = types[key]
            coerced = (
                float(value)
                if kind in (float, "float")
                else int(value)
                if kind in (int, "int")
                else str(value).strip()
            )
            setattr(self, key, coerced)
        if self.ftp <= 0 or self.mass_kg <= 0:
            raise ValueError("FTP and mass must be positive")
        self.difficulty = min(max(self.difficulty, 0.0), 1.0)

    def public(self) -> dict[str, Any]:
        """Settings for the UI: secrets are reported as set/unset only."""
        out = {k: v for k, v in asdict(self).items() if k not in self.SECRET}
        out |= {f"{k}_set": bool(getattr(self, k)) for k in self.SECRET}
        out["strava_connected"] = bool(self.strava_refresh_token)
        return out
