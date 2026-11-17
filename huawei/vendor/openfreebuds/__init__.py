"""Minimal vendored OpenFreebuds namespace package (HuaweiPodsCC variant).

Upstream's ``openfreebuds/__init__.py`` re-exports the desktop application's
entry points::

    from .main import create
    from .manager.generic import IOpenFreebuds
    from .driver import is_device_supported, DEVICE_TO_DRIVER_MAP
    from .constants import *

HuaweiPodsCC only vendors the driver / protocol layer, so the application-level
re-exports (``.main`` and ``.manager``) are dropped here; those packages are not
part of the vendored tree and importing them would fail.  The driver registry is
still re-exported because it *is* part of the vendored tree.

This is an import-boundary adjustment only -- no upstream implementation logic
is changed.  See ``../ORIGIN.md`` ("Modifications").
"""

from .driver import DEVICE_TO_DRIVER_MAP, is_device_supported
from .constants import *

__all__ = ["DEVICE_TO_DRIVER_MAP", "is_device_supported"]
