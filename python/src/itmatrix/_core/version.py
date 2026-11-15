"""The SDK's own version, the request header that declares it, and the upgrade notice.

Every request carries ``X-ITM-SDK: itmatrix-python/<version>``, the same name
the stream declares in its hello. The API may answer ``X-ITM-SDK-Current`` with
the newest release; when this install is behind on the major or minor version,
one ``ITMUpgradeWarning`` per process names the upgrade command. Patch releases
never warn, the notice never raises, and ``ITMATRIX_NO_UPGRADE_CHECK=1`` (or an
ordinary ``warnings`` filter) silences it.
"""

from __future__ import annotations

import importlib.metadata
import os
import re
import threading
import warnings
from functools import lru_cache

SDK_HEADER = "X-ITM-SDK"
CURRENT_HEADER = "X-ITM-SDK-Current"
SILENCE_ENV = "ITMATRIX_NO_UPGRADE_CHECK"
# Used only when the package metadata is missing (a source checkout on sys.path).
_FALLBACK_VERSION = "1.2.1"
_RELEASE = re.compile(r"^\s*v?(\d+)\.(\d+)")


class ITMUpgradeWarning(UserWarning):
    """A newer minor or major release of ``itmatrix`` is available."""


@lru_cache(maxsize=1)
def sdk_version() -> str:
    try:
        return importlib.metadata.version("itmatrix")
    except importlib.metadata.PackageNotFoundError:
        return _FALLBACK_VERSION


def client_name() -> str:
    return f"itmatrix-python/{sdk_version()}"


def _release(value: str) -> tuple[int, int] | None:
    match = _RELEASE.match(value)
    return (int(match[1]), int(match[2])) if match else None


_lock = threading.Lock()
_warned = False


def notice_current(advertised: str | None) -> None:
    """Warn once per process when the API advertises a newer minor or major."""
    global _warned
    if _warned or not advertised:
        return
    if os.environ.get(SILENCE_ENV, "").strip() not in ("", "0"):
        return
    installed, current = _release(sdk_version()), _release(advertised)
    if installed is None or current is None or installed >= current:
        return
    with _lock:
        if _warned:
            return
        _warned = True
    warnings.warn(
        f"itmatrix {sdk_version()} is behind the current release {advertised.strip()}; "
        f"upgrade with `pip install -U itmatrix`. Set {SILENCE_ENV}=1 to silence this.",
        ITMUpgradeWarning,
        stacklevel=2,
    )


def _reset_for_tests() -> None:
    global _warned
    with _lock:
        _warned = False
