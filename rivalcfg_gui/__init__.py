"""rivalcfg-gui -- a GTK3 configuration tool for SteelSeries mice.

This module is kept deliberately import-light.  The application itself pulls in
PyGObject, which is not importable in headless environments, and the test suite
imports ``rivalcfg_gui.device_core`` and friends.  Importing the package must
therefore not drag GTK in, so ``main`` is bound lazily rather than imported here.
"""

__version__ = "1.6.0"

__all__ = ["__version__", "main"]


def main():
    """Launch the GUI.  Imported lazily; see the module docstring."""
    from .app import main as _main

    return _main()
