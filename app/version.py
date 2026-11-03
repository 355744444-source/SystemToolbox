# -*- coding: utf-8 -*-
"""版本与出品信息的**唯一出处**。

打包脚本（`build.spec`）在构建时 import 这里，把同一组字符串写进 exe 的
版本资源；界面标题、HTML 报告页脚、`--version`、`--selftest` 也都读这里。

以前 `VERSION = "1.17.0"` 藏在 `core/report.py` 里 —— 报告模块并不该
决定产品叫什么版本，而且改版本号时容易漏掉别处的硬编码。
这个模块**不准 import 任何 Qt / app 内其他模块**，打包脚本要能在
没有 Qt 环境的情况下 import 它。
"""

APP_NAME = "SystemToolbox"                  # 可执行文件名
APP_TITLE = "系统万能工具箱 SystemToolbox"     # 界面标题 / 报告页脚
PRODUCT_NAME = "系统万能工具箱 SystemToolbox"  # 属性里的「产品名称」
INTERNAL_NAME = "SystemToolbox"              # 属性里的「内部名称」
FILE_DESC = "系统万能工具箱"                   # exe 属性里的「文件说明」
COMPANY = "江苏振远智能科技有限公司"
COPYRIGHT = "Copyright © 2026 江苏振远智能科技有限公司"
HOMEPAGE = ""                                # 暂未对外发布，留空

VERSION = "2.0.0"


def version_tuple() -> tuple:
    """`(2, 0, 0, 0)` —— Windows 版本资源要四段数字。"""
    parts = []
    for seg in VERSION.split(".")[:4]:
        digits = "".join(ch for ch in seg if ch.isdigit())
        parts.append(int(digits or 0))
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts)
