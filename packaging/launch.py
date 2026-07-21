"""Bundle entry point.

PyInstaller executes its entry script as a top-level module, not as part of a
package, so `jerry/__main__.py`'s relative imports (`from .server import ...`)
raise ImportError inside the .app while working perfectly from source. This
launcher exists solely to enter through absolute imports instead.
"""

from jerry.__main__ import main

if __name__ == "__main__":
    main()
