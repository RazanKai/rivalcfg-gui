#!/usr/bin/env python3
import os
import re

from setuptools import setup

HERE = os.path.dirname(os.path.abspath(__file__))


def _read_version():
    """Single-source the version from the package's ``__init__``.

    Read with a regex rather than by importing: ``rivalcfg_gui`` is deliberately
    import-light, but keeping setup.py free of the package import means it also
    works while the tree is half-built.
    """
    path = os.path.join(HERE, "rivalcfg_gui", "__init__.py")
    with open(path, encoding="utf-8") as fh:
        match = re.search(r'^__version__ = "([^"]+)"', fh.read(), re.M)
    if match is None:
        raise RuntimeError("__version__ not found in rivalcfg_gui/__init__.py")
    return match.group(1)


setup(
    name="rivalcfg-gui",
    version=_read_version(),
    description="GTK3 GUI configuration tool for SteelSeries mice",
    long_description="A Linux desktop application for configuring SteelSeries mouse settings including DPI, polling rate, RGB lighting, and button mappings through a modern GTK3 interface.",
    author="MrGodzilla38",
    author_email="oyunustasigodzilla@gmail.com",
    url="https://github.com/MrGodzilla38/rivalcfg-gui",
    license="GPL-3.0-or-later",
    packages=["rivalcfg_gui"],
    # The .po sources ship alongside the compiled .mo so the translation workflow
    # in the README works from an installed copy, not just from a git checkout.
    package_data={
        "rivalcfg_gui": [
            "assets/*",
            "locales/*/LC_MESSAGES/*.po",
            "locales/*/LC_MESSAGES/*.mo",
        ],
    },
    python_requires=">=3.10",
    # PyGObject and pycairo are deliberately NOT listed: on Linux they are system
    # packages (python-gobject / python-cairo), and pip cannot supply them
    # reliably. See the README's install section.
    install_requires=[
        "rivalcfg>=4.17.0",
    ],
    entry_points={
        "console_scripts": [
            "rivalcfg-gui = rivalcfg_gui:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        # No `License ::` classifier: it is deprecated in favour of the SPDX
        # `license` expression above, and setuptools warns about it.
        "Operating System :: POSIX :: Linux",
        "Topic :: System :: Hardware :: Hardware Drivers",
    ],
)
