"""Entry point for ``python -m rivalcfg_gui``.

The distro packages (Debian, Flatpak, AUR) launch the app this way, since the
modules use package-relative imports and cannot be run as a bare script path.
"""

from .app import main

if __name__ == "__main__":
    main()
