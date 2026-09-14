"""Isolated-mode root entry: only import from the root-owned app directory."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from smart_cache import main
if __name__ == '__main__':
    main()
