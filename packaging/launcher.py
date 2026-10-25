"""PyInstaller entry script. The package uses relative imports, so it can't be the script itself."""
import sys

from retrozashi.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
