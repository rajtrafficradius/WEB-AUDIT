"""Build the deduplicated V18-compatible v19 acceptance package."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from exporters.v18_compatible import main

if __name__ == "__main__":
    raise SystemExit(main())
