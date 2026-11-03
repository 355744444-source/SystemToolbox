# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：单文件、无控制台、带图标。

构建：pyinstaller --noconfirm --clean build.spec

体积控制分两层：
1. `excludes` —— 拦在 import 阶段（第三方库）；
2. `DROP_BINARIES` / `DROP_DATA` —— 拦在 Analysis 之后（PyQt5 钩子会无条件
   把整套 Qt 带进来，光靠 excludes 挡不住 DLL）。
单文件版每次启动都要把包内内容解到临时目录，所以砍掉的无用 DLL
既省磁盘，也直接加快冷启动。
"""

import os
import sys

ROOT = os.path.abspath(os.getcwd())

# ---------------------------------------------------------------- 版本资源
# v1.17.0 之前这里是 `version=None`：exe 右键属性里「文件版本 / 产品名 /
# 公司名 / 版权」全是空的。对一个要交到客户机房、还要过杀软与装机白名单的
# 工具来说，属性空白比版本号写错更糟 —— 写清楚归属比写清楚版本更难伪造。
#
# 唯一出处是 `app/version.py`：界面标题、HTML 页脚、`--selftest` 全读那一份，
# 这里只负责把它翻译成 Windows 版本资源。
sys.path.insert(0, ROOT)
from app.version import (APP_NAME, APP_TITLE, COMPANY, COPYRIGHT,  # noqa: E402
                         FILE_DESC, HOMEPAGE, INTERNAL_NAME,
                         PRODUCT_NAME, VERSION, version_tuple)


def _write_version_resource() -> str:
    """按 VSVersionInfo 文本格式生成版本资源文件，返回路径。

    中文串一律过 `%r`（生成 `'江苏振远智能科技有限公司'`），避免引号、
    反斜杠在拼字符串时被吃掉；文件以 UTF-8 落盘，PyInstaller 按 UTF-8 读。
    """
    ver = version_tuple()

    def s(key, val):
        return "StringStruct(%r, %r)" % (key, val)

    rows = [
        s("CompanyName", COMPANY),
        s("FileDescription", FILE_DESC),
        s("FileVersion", VERSION),
        s("InternalName", INTERNAL_NAME),
        s("LegalCopyright", COPYRIGHT),
        s("OriginalFilename", APP_NAME + ".exe"),
        s("ProductName", PRODUCT_NAME),
        s("ProductVersion", VERSION),
    ]
    if HOMEPAGE:
        rows.append(s("LegalTrademarks", HOMEPAGE))
    body = ",\n                ".join(rows)
    text = (
        "VSVersionInfo(\n"
        "  ffi=FixedFileInfo(filevers=%r, prodvers=%r, mask=0x3f, flags=0x0,\n"
        "                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),\n"
        "  kids=[\n"
        "    StringFileInfo([\n"
        "      StringTable('080404b0', [\n"
        "                %s\n"
        "      ])\n"
        "    ]),\n"
        "    VarFileInfo([VarStruct('Translation', [2052, 1200])])\n"
        "  ]\n"
        ")\n" % (ver, ver, body)
    )
    out_dir = os.path.join(ROOT, "build")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "version_info.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


VERFILE = _write_version_resource()

# 运行期确实用不到的大件（体积按 v1.7.0 的打包结果实测，packed/unpacked）：
#   opengl32sw.dll      7.40M / 19.95M  软件 OpenGL 回落实现
#   d3dcompiler_47.dll  1.67M /  3.98M  ANGLE 的着色器编译
#   libGLESv2.dll       1.22M /  3.23M  ANGLE 本体
#   Qt5Quick.dll        1.55M /  3.96M  QML 运行时
#   Qt5Qml.dll          1.32M /  3.43M
#   Qt5QmlModels.dll    0.18M
#   Qt5Network.dll      0.54M /  1.28M  代码里零引用（网络诊断走 Win32 API）
#   Qt5DBus.dll         0.18M /  0.42M
#   Qt5Svg.dll          0.13M /  0.32M  图标全是 QPainter 画的，没有 SVG
#   qwebgl.dll / qminimal.dll 0.61M     只用 qwindows + qoffscreen 两个平台插件
# 本程序全是 QWidget 光栅绘制，不创建任何 GL 上下文，Windows 平台插件
# 只在真要 GL 时才去加载 ANGLE 三件套，所以删掉是安全的（已用打包版自检验证）。
DROP_BINARIES = {
    "opengl32sw.dll", "d3dcompiler_47.dll", "libglesv2.dll",
    "qt5quick.dll", "qt5qml.dll", "qt5qmlmodels.dll",
    "qt5network.dll", "qt5dbus.dll", "qt5svg.dll",
    "qwebgl.dll", "qminimal.dll",
}

# 没有 QTranslator，界面文案全是自己写的中文；Qt 自带翻译用不上
DROP_DATA = ("pyqt5/qt5/translations/",)

a = Analysis(
    ["main.py"],
    pathex=[ROOT],
    binaries=[],
    # 必须带上图标：单文件模式下运行期会解到 sys._MEIPASS，
    # QApplication.setWindowIcon 才能拿到，否则任务栏/Alt-Tab 画成系统占位图
    datas=[("assets/app.ico", "assets")],
    hiddenimports=["openpyxl", "openpyxl.cell._writer", "openpyxl.styles"],
    hookspath=[],
    runtime_hooks=[],
    # 只用到 QtCore/QtGui/QtWidgets，其余 Qt 模块与科学计算栈全部排除，控制体积
    excludes=[
        "numpy", "pandas", "scipy", "matplotlib", "PIL", "tkinter", "pytest",
        "IPython", "notebook", "sqlite3.fts", "unittest.mock.mock",
        # 这两个包工程里零引用（lxml 5.7MB、pythonnet 1.5MB），
        # 是环境里装的其他工具带进来的，PyInstaller 顺着 openpyxl 扫到了
        "lxml", "pythonnet", "clr_loader", "clr",
        "PyQt5.QtWebEngineWidgets", "PyQt5.QtWebEngineCore", "PyQt5.QtWebEngine",
        "PyQt5.QtQml", "PyQt5.QtQuick", "PyQt5.QtQuickWidgets", "PyQt5.QtMultimedia",
        "PyQt5.QtMultimediaWidgets", "PyQt5.QtNetwork", "PyQt5.QtSql", "PyQt5.QtTest",
        "PyQt5.QtPrintSupport", "PyQt5.QtBluetooth", "PyQt5.QtDBus", "PyQt5.QtDesigner",
        "PyQt5.QtHelp", "PyQt5.QtLocation", "PyQt5.QtNfc", "PyQt5.QtOpenGL",
        "PyQt5.QtPositioning", "PyQt5.QtScript", "PyQt5.QtSensors",
        "PyQt5.QtSerialPort", "PyQt5.QtSvg", "PyQt5.QtXml", "PyQt5.QtXmlPatterns",
        "PyQt5.QtWebSockets", "PyQt5.QtWinExtras", "PyQt5.Qt3DCore",
    ],
    noarchive=False,
)

# 过滤 Qt 运行时 DLL 与翻译文件（见文件头说明）
a.binaries = [b for b in a.binaries
              if os.path.basename(b[0]).lower() not in DROP_BINARIES]
a.datas = [d for d in a.datas
           if not d[0].replace("\\", "/").lower().startswith(DROP_DATA)]

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SystemToolbox",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "assets", "app.ico"),
    version=VERFILE,
)
