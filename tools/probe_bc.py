# -*- coding: utf-8 -*-
"""探针：确认转储头部的蓝屏码偏移 + 本机事件日志里的蓝屏记录实况。

只读，不写任何系统位置。结果写 probe_bc.log。
"""
import io
import os
import struct
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

LOG = os.path.join(ROOT, "probe_bc.log")
_buf = []


def p(*a):
    _buf.append(" ".join(str(x) for x in a))


def dump_log():
    io.open(LOG, "w", encoding="utf-8").write("\n".join(_buf) + "\n")


def head_fields(path):
    """按 DUMP_HEADER64 读前 0x60 字节。"""
    out = {"path": path, "size": os.path.getsize(path)}
    with open(path, "rb") as f:
        h = f.read(0x60)
    out["sig"] = h[:4]
    out["valid"] = h[4:8]
    out["magic_raw"] = h[:8]
    is64 = h[4:8] == b"DU64"
    out["is64"] = is64
    if is64:
        out["code"] = struct.unpack_from("<I", h, 0x38)[0]
        out["params"] = [struct.unpack_from("<Q", h, 0x40 + i * 8)[0] for i in range(4)]
        out["image_type"] = struct.unpack_from("<I", h, 0x30)[0]
        out["cpus"] = struct.unpack_from("<I", h, 0x34)[0]
    else:
        out["code"] = struct.unpack_from("<I", h, 0x28)[0]
        out["params"] = [struct.unpack_from("<I", h, 0x2C + i * 4)[0] for i in range(4)]
        out["image_type"] = struct.unpack_from("<I", h, 0x20)[0]
        out["cpus"] = struct.unpack_from("<I", h, 0x24)[0]
    # 交叉核对：0x400 处（部分资料写这里）
    with open(path, "rb") as f:
        f.seek(0x400)
        tail = f.read(0x20)
    if len(tail) >= 4:
        out["code_at_0x400"] = struct.unpack_from("<I", tail, 0)[0]
    return out


def main():
    p("== 转储头部探针 ==")
    cands = []
    live = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                        "LiveKernelReports")
    if os.path.isdir(live):
        for cur, _d, files in os.walk(live):
            for f in files:
                if f.lower().endswith(".dmp"):
                    cands.append(os.path.join(cur, f))
    md = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Minidump")
    if os.path.isdir(md):
        for f in os.listdir(md):
            if f.lower().endswith((".dmp", ".mdmp")):
                cands.append(os.path.join(md, f))
    for extra in ("samples/kernel_dpc_watchdog_133.dmp",
                  "samples/dpc_watchdog_133.dmp",
                  "tests/data/kernel_dpc_watchdog_133.dmp"):
        fp = os.path.join(ROOT, extra)
        if os.path.isfile(fp):
            cands.append(fp)
    # 全盘找一下 selftest 用的样例转储
    for root in (ROOT, os.path.join(ROOT, "tests"), os.path.join(ROOT, "samples")):
        for cur, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in ("build_v1.16.0", "archive", ".git")]
            for f in files:
                if f.lower().endswith(".dmp") and "watchdog" in f.lower():
                    cands.append(os.path.join(cur, f))
    seen = set()
    for c in cands:
        key = os.path.normcase(c)
        if key in seen:
            continue
        seen.add(key)
        try:
            info = head_fields(c)
            p("")
            p("file:", os.path.basename(c), "size=%d" % info["size"])
            p("  sig=%r valid=%r is64=%s" % (info["sig"], info["valid"], info["is64"]))
            p("  code@0x38=0x%08X  code@0x400=0x%08X"
              % (info["code"], info.get("code_at_0x400", -1) & 0xFFFFFFFF))
            p("  params=", [hex(x) for x in info["params"]])
            p("  image_type=0x%X cpus=%d" % (info["image_type"], info["cpus"]))
        except Exception as exc:  # noqa: BLE001
            p("  FAILED %s: %s" % (c, exc))

    p("")
    p("== 事件日志蓝屏记录（dumpcfg.scan_crashes）==")
    try:
        from app.core import dumpcfg
        import time
        t0 = time.perf_counter()
        evs = dumpcfg.scan_crashes(limit=200)
        dt = (time.perf_counter() - t0) * 1000
        p("cost=%.0fms count=%d" % (dt, len(evs)))
        for e in evs[:12]:
            p("  %s kind=%-8s code=0x%08X prov=%s" %
              (e.time_text, e.kind, e.code, e.provider_short))
    except Exception:
        p(traceback.format_exc())

    p("")
    p("== 转储目录现状 ==")
    from app.core import dumpcfg
    s = dumpcfg.read(with_events=False)
    p("enabled=%d type=%s" % (s.enabled, s.type_text))
    p("minidump_dir=%s exists=%s" % (s.minidump_path, os.path.isdir(s.minidump_path)))
    p("dump_file=%s exists=%s" % (s.dump_file_path, os.path.isfile(s.dump_file_path)))
    p("dumps=%d live_reports=%d" % (len(s.dumps), len(s.live_reports)))
    for d in s.dumps:
        p("  dump:", d.path, d.size_text, d.time_text, d.kind)
    for r in s.live_reports:
        p("  live:", r)


try:
    main()
except Exception:
    p(traceback.format_exc())
finally:
    dump_log()
