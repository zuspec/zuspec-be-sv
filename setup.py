"""Supplies the one field pyproject.toml declares dynamic: the version.

Everything else about this package stays in pyproject.toml. This file exists
so the version can live in src/zuspec/be/sv/__version__.py and reach setuptools
from there, making that file the single place the number is written down.

The mechanism is pssparser's (see its setup.py): exec() the version file in an
empty namespace and read VERSION back out. exec() treats it as a standalone
source file, so nothing has to be importable and sys.path is never consulted.

A `[tool.setuptools.dynamic] version = {attr = ...}` entry is the obvious
spelling and was tried first. It builds correctly, but it breaks the shared
zuspec-pybuild workflow, whose development-build step rewrites pyproject.toml
with

    sed -e 's%version.*=.*"\(.*\)"%version="\1${suffix}"%g'

That pattern assumes a static `version = "x.y.z"`. Against an attr: entry it
captures the dotted attribute path and leaves the closing brace stranded:

    version="zuspec.be.sv.__version__.VERSION.dev<run-id>+gh"}

which is invalid TOML, and the build dies in `python -m build` before
setuptools is ever reached. With the version supplied from here, pyproject.toml
has no `version = "..."` line for that sed to match, so the workflow's
rewrite is a no-op and the SUFFIX edit it makes to the version file is the
only thing that takes effect -- which is what was intended.
"""

import os

from setuptools import setup

proj_dir = os.path.dirname(os.path.abspath(__file__))


def _get_version():
    version_file = os.path.join(proj_dir, "src", "zuspec", "be", "sv", "__version__.py")
    glb = {}
    with open(version_file) as f:
        exec(f.read(), glb)
    return glb["VERSION"]


setup(version=_get_version())
