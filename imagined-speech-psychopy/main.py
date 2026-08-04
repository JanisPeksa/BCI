"""Repository launcher for the imagined-speech application."""

import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from imagined_speech.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
