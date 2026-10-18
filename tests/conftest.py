import sys
from pathlib import Path

# Make the project root importable so tests can `import tilecount` without installing it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
