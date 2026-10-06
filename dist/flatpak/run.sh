#!/bin/sh
# The modules use package-relative imports, so rivalcfg_gui.py can no longer be
# run as a bare script path.  Launch the package instead; its parent directory
# goes on PYTHONPATH.  No RIVALCFG_GUI_LOCALE_DIR is needed any more: the
# catalogs live inside the package and resolve relative to the module.
export PYTHONPATH="/app/lib/rivalcfg-gui${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m rivalcfg_gui "$@"
