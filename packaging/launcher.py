"""PyInstaller entry point; package-relative imports live in desktop_chibi."""
from desktop_chibi.runtime import main

if __name__ == '__main__':
    raise SystemExit(main())
