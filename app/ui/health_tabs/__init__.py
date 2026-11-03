# -*- coding: utf-8 -*-
"""体检工作台的十个页签，一个页签一个 mixin。

`HealthPage` 继承它们，方法仍是同一个 `self` 上的方法 ——
拆分只为「一个文件别一千七百行」，不是重新设计对象关系。
"""
from .dev import DevTabMixin  # noqa: F401
from .upgrade import UpgradeTabMixin  # noqa: F401
from .perf import PerfTabMixin  # noqa: F401
from .security import SecurityTabMixin  # noqa: F401
from .component import ComponentTabMixin  # noqa: F401
from .disk import DiskTabMixin  # noqa: F401
from .hwevent import HwTabMixin  # noqa: F401
from .dumpcfg import DumpCfgTabMixin  # noqa: F401
from .pathenv import PathEnvTabMixin  # noqa: F401
from .drivers import DriversTabMixin  # noqa: F401

__all__ = [
    "DevTabMixin",
    "UpgradeTabMixin",
    "PerfTabMixin",
    "SecurityTabMixin",
    "ComponentTabMixin",
    "DiskTabMixin",
    "HwTabMixin",
    "DumpCfgTabMixin",
    "PathEnvTabMixin",
    "DriversTabMixin",
]
