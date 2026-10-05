from pathlib import Path
from typing import Protocol

from rollforge_schemas.domain import TrialIdentity


class HarborRunner(Protocol):
    """Return the native result directory; never fabricate Harbor TrialConfig APIs."""

    async def run(self, trial: TrialIdentity, output_dir: Path) -> Path: ...
