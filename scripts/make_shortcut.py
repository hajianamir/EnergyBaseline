"""Create EnergyBaseline.lnk (custom .ico) on the Desktop and in the project folder (Windows)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from energybaseline.launcher import make_shortcuts  # noqa: E402

if __name__ == "__main__":
    made = make_shortcuts(force=True)
    print("Created:", *made) if made else print("No shortcut created (Windows only).")
