# -*- coding: utf-8 -*-
"""端到端验证：线程内回收站删除、defrag 只读分析的真实输出。

只在临时目录里造文件，不碰任何真实用户数据。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", os.environ.get("CLS_QPA", "windows"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "probe_clean.log")


def main() -> int:
    lines = []
    try:
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QApplication

        from app.core import cleaner, disk
        from app.ui.tables import Worker

        app = QApplication(sys.argv)

        # ---- 1. 线程内回收站删除
        tmp = tempfile.mkdtemp(prefix="cls_probe_")
        files = []
        for i in range(4):
            p = os.path.join(tmp, f"probe_{i}.tmp")
            with open(p, "wb") as f:
                f.write(b"x" * 2048)
            files.append((p, 2048))
        holder = {}

        w = Worker(lambda tick, stop: cleaner.delete_files(
            files, use_recycle=True,
            progress=lambda a, b: tick(a, b, "正在删除")))
        loop_done = {"v": False}
        w.done.connect(lambda r: (holder.update(rep=r), loop_done.update(v=True)))
        w.failed.connect(lambda m: (holder.update(err=m), loop_done.update(v=True)))
        w.start()
        t0 = time.time()
        while not loop_done["v"] and time.time() - t0 < 20:
            app.processEvents()
            time.sleep(0.02)
        rep = holder.get("rep")
        left = [p for p, _s in files if os.path.exists(p)]
        lines.append(f"recycle-thread: rep={rep} remaining={len(left)} "
                     f"err={holder.get('err', '')}")
        try:
            os.rmdir(tmp)
        except OSError:
            pass

        # ---- 2. defrag 只读分析（真实子进程 + 中文输出解码）
        vols = [v for v in disk.list_volumes() if v.letter == "C:"]
        if vols:
            action = disk.build_action(vols[0], "analyze")
            out_lines: list[str] = []
            code, got = disk.run_action(action, on_line=out_lines.append)
            lines.append(f"defrag-analyze: exit={code} lines={len(got)}")
            for ln in got[:12]:
                lines.append("  | " + ln)
            pcts = [disk.parse_defrag_percent(x) for x in got]
            lines.append("  percents=" + str([p for p in pcts if p is not None][:8]))

        # ---- 3. 回收站信息
        lines.append(f"recycle-bin: {cleaner.recycle_bin_info()}")
        lines.append(f"is_admin: {cleaner.is_admin()}")
    except Exception as exc:        # noqa: BLE001
        lines.append("FAILED: " + repr(exc))
        lines.append(traceback.format_exc())
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
