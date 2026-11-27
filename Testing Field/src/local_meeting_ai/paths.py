"""App-owned paths supplied explicitly; never discovers user directories."""
from dataclasses import dataclass
from pathlib import Path
@dataclass(frozen=True)
class AppPaths:
    root: Path
    @property
    def meetings(self): return self.root / "media"
