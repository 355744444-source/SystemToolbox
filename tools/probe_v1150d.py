# -*- coding: utf-8 -*-
"""drivers.audit 真机跑一遍：数字、耗时、结论卡、缓存效果。"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
LOG = os.path.join(ROOT, "probe_v1150d.log")


def main():
    from app.core import drivers

    out = []

    def w(m=""):
        out.append(m)

    # 第一遍（冷缓存）
    for rnd in (1, 2):
        t0 = time.perf_counter()
        marks = []
        rep = drivers.audit(progress=lambda d, t, s: marks.append((d, t, s)))
        wall = (time.perf_counter() - t0) * 1000
        w("== 第 %d 遍：audit %.0fms（内部 %.0fms）缓存命中 %d/%d 超预算=%s"
          % (rnd, wall, rep.elapsed_ms, rep.cache_hits, len(rep.files),
             rep.signature_cut))
        w("   tick 次数=%d 最后一条=%s" % (len(marks), marks[-1] if marks else None))
    w()
    w("驱动包 %d（内置 %d + 第三方 %d）；驱动服务 %d；加载模块 %d"
      % (len(rep.packages), rep.builtin_count, len(rep.third_packages),
         len(rep.services), rep.modules))
    w("驱动文件 %d；无签名 %d（其中已加载 %d）；签名可疑 %d；文件缺失服务 %d"
      % (len(rep.files), len(rep.unsigned), len(rep.unsigned_loaded),
         len(rep.suspicious_sign), len(rep.missing_files)))

    w()
    w("== 第三方驱动包 ==")
    for p in rep.third_packages:
        w("  %-10s %-34s %-38s %-11s %s"
          % (p.inf, p.original_inf[:34], p.signer_text[:38], p.installed or "-",
             p.size_text))
        w("      来源 %s" % p.source_text)

    w()
    w("== 有问题的驱动文件 ==")
    for f in rep.files:
        if not f.problem:
            continue
        w("  %-22s %-8s loaded=%-5s start=%-4s svc=%s"
          % (f.name, f.level_text, f.loaded, f.start_text,
             ",".join(f.services[:2]) or "-"))
        w("      %s" % (drivers.sg.describe(f.info) if f.info else "未扫描"))

    w()
    w("== 文件缺失的驱动服务 ==")
    for s in rep.missing_files[:12]:
        w("  %-24s start=%-4s %s" % (s.name, s.start_text, s.file))
    w("  合计 %d 个" % len(rep.missing_files))

    w()
    w("== 结论 ==")
    w("  verdict=%s" % (drivers.verdict(rep),))
    for f in drivers.build_findings(rep):
        w("  [%s] %s" % (f.severity, f.title))
        w("        %s" % f.detail[:110])

    with open(LOG, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
