# Vendored third-party source

This directory contains source code copied from an upstream open-source
project. It is not written by this project's authors.

## Upstream

- Project: **OpenFreebuds**
- Repository: <https://github.com/melianmiko/OpenFreebuds>
- Commit (HEAD at vendoring time): `59b77e334f438fb670ba8873e6e721bfabdb7238`
- Date: 2026-09-14
- Version: `0.18.1` (per `pyproject.toml`)

## License

Upstream OpenFreebuds is licensed under the **GNU General Public License v3.0
(GPL-3.0)**. The vendored files retain their original copyright headers and
license terms.

Because this vendored code is included in, and linked with, HuaweiPodsCC, the
**HuaweiPodsCC project as a whole is therefore also distributed under GPL-3.0**.
See the `LICENSE` file in the project root.

## Scope of vendoring

Only the modules required by the Huawei SPP driver are vendored:

- `openfreebuds/driver/huawei/**` — Huawei protocol layer (frame codec,
  handlers, per-model drivers, upstream unit tests)
- `openfreebuds/driver/generic/**` — generic driver base + SPP transport
  abstraction
- `openfreebuds/driver/generic_debug/**` — file-backed debug driver
- `openfreebuds/driver/constants.py` — device-name → driver registry
- `openfreebuds/driver/utils.py`
- `openfreebuds/driver/__init__.py`
- `openfreebuds/exceptions/**`
- `openfreebuds/constants.py`
- `openfreebuds/utils/**` — logger / event bus helpers
- `openfreebuds/__init__.py` — see Modifications below

## Modifications

The vendored tree is NOT byte-for-byte identical to upstream. Every deviation
from upstream `59b77e33` / v0.18.1 is listed here, in full. Nothing is hidden.
All deviations are *import-boundary* or *optional-dependency* adjustments; no
protocol/handler logic has been rewritten. Changes are confined to the two files
below.

### M1. `openfreebuds/utils/stupid_rpc.py` — make `aiohttp` optional

- **What changed:** the module-level
  ```python
  import aiohttp
  from aiohttp import web
  from aiohttp.web_response import json_response
  ```
  was wrapped in `try: ... except ImportError: ...`, setting `aiohttp = None`,
  `web = None`, `json_response = None` when the package is absent. The `rpc`
  decorator now returns the wrapped function unchanged ("pass-through") when
  `aiohttp is None`.
- **Why:** `aiohttp` was pulled in transitively and unconditionally via
  `utils/event_bus.py` → `utils/stupid_rpc.py`, meaning merely instantiating any
  driver required it. `aiohttp` (plus its C-extension dependency chain:
  `multidict`, `yarl`, `frozenlist`, `aiosignal`) is used **only** by
  OpenFreebuds' optional HTTP-RPC layer, which HuaweiPodsCC never invokes (we
  run the driver in the `standalone` role inside our own asyncio loop; the
  `role == "client"` branch is never taken). This keeps HuaweiPodsCC's runtime
  dependency set identical to the vivo build (PySide6 only).
- **Behaviour when `aiohttp` is absent:** `@rpc` becomes a no-op decorator and
  the function is called directly. This exactly matches the upstream
  `role != "client"` path. The `do_rpc_request`, `test_online`,
  `_handle_rpc_call` and `run_rpc_server` helpers remain untouched; they are
  dead code in our usage and would only raise if actually called without
  `aiohttp`.
- **Behaviour when `aiohttp` IS installed:** identical to upstream.
- **Rollback:** if this ever proves too invasive, revert this file to upstream
  and add `aiohttp>=3.9` to `requirements.txt` (fallback plan, not used).

### M2. `openfreebuds/utils/__init__.py` — (no change)

Listed for completeness: this file is upstream-verbatim.

### M3. `openfreebuds/__init__.py` — minimal namespace package

- **What changed:** upstream's root `__init__.py` re-exports the desktop
  application entry points:
  ```python
  from .main import create
  from .manager.generic import IOpenFreebuds
  from .driver import is_device_supported, DEVICE_TO_DRIVER_MAP
  from .constants import *
  ```
  The `.main` and `.manager` re-exports were removed (those packages are not
  part of the vendored tree). The driver registry re-export and
  `from .constants import *` were kept, since both are vendored.
- **Why:** importing `openfreebuds` raised `ModuleNotFoundError: No module named
  'openfreebuds.main'` because only the driver subtree is vendored. This is a
  pure import-boundary fix; no logic changed.
- **Not modified:** `openfreebuds/constants.py`, `driver/constants.py`,
  `driver/utils.py`, `driver/__init__.py`, `exceptions/__init__.py` are all
  upstream-verbatim.

## Not part of upstream / replacements

- `openfreebuds_backend/` — this is **our own** stdlib-only stand-in for the
  upstream separate distribution `openfreebuds-backend`. Upstream's Windows
  branch imports `winsdk`; we deliberately avoid that. The replacement provides
  only the two symbols actually referenced by the vendored tree:
  - `get_app_storage_path()` — used by `openfreebuds.constants`
  - `bt_is_connected(address)` — used by `openfreebuds.driver.generic.base`
  `bt_is_connected` reuses the project's own `bthprops.cpl` Bluetooth
  enumeration (`BluetoothFindFirstDevice` / `BLUETOOTH_DEVICE_INFO.fConnected`).
  No third-party imports, and specifically no `winsdk`. See that package's
  module docstring for details.

`dummy.py` / `exception.py` inside `openfreebuds_backend/` are also ours, kept
only for interface completeness; nothing in the vendored tree imports them.

## Integration boundary

Import paths are adjusted only at the integration boundary (see
`huawei/bridge.py`), never inside the vendored `openfreebuds/` tree — with the
sole exception of the two files documented under **Modifications** above.

## Stage 2 (2026-10-24): integration layer notes
- `huawei/transport.py`, `huawei/bridge.py`, `huawei/controller.py`,
  `huawei/demo_transport.py` are **this project's** files (not vendored).
- The only vendor runtime quirk handled at the integration boundary (in
  `huawei/bridge.py`, not inside `openfreebuds/`): the name collision between
  the namespace package `openfreebuds.driver.generic/` and the module
  `openfreebuds.driver.huawei.driver.generic.py`. Because
  `openfreebuds/driver/__init__.py` star-imports the huawei driver, the
  `generic` attribute of `openfreebuds.driver` is shadowed by the huawei
  module; `import openfreebuds.driver.generic.spp` would then fail with
  `cannot import name 'spp'`. Fix: re-bind the attribute to the real package
  from `sys.modules` before importing `spp`/`spp_transport`. No vendor file
  was changed for this.

## Stage 3 (2026-10-26): merged into VivoPodsCC

- This vendor tree is now shipped inside **VivoPodsCC** (the dual-brand build),
  not a standalone HuaweiPodsCC project. `huawei/**` is still our integration
  layer; nothing inside `openfreebuds/**` was touched for the merge.
- **M4. `openfreebuds_backend/__init__.py`** (our own stand-in, not upstream):
  `get_app_storage_path()` now returns `%LOCALAPPDATA%\VivoPodsCC` instead of
  `%LOCALAPPDATA%\HuaweiPodsCC`, so the merged app keeps a single per-user
  directory. Only the folder name changed; the function signature and semantics
  are identical.
- `openfreebuds` / `openfreebuds_backend` are collected into the EXE via
  `collect_submodules(...)` in `VivoPodsCC.spec`, because the vendored tree is
  added to `sys.path` at runtime and PyInstaller's static analysis cannot see it.
