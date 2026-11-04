from __future__ import annotations

from functools import lru_cache

from ..config import get_config
from .base import Endpoint, HyperVDriver, HyperVError

__all__ = ["Endpoint", "HyperVDriver", "HyperVError", "get_driver"]


@lru_cache(maxsize=1)
def get_driver() -> HyperVDriver:
    cfg = get_config()
    if cfg.hyperv_driver == "fake":
        from .fake import FakeDriver

        return FakeDriver(cfg.fake_state_file)
    from .psrp import PsrpDriver

    return PsrpDriver()
