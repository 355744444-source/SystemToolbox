# -*- coding: utf-8 -*-
"""报告导出：单文件 HTML 现场报告 + Excel 明细。

HTML 面向「发给客户/售后/研发」的场景：自带样式、离线可看、可直接打印，
所以配色走浅色（深色打印会糊成一片）。
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from html import escape

from ..version import APP_TITLE, COMPANY, VERSION
from . import knowledge as kb

BG = "#F2F5F9"
SURFACE = "#FFFFFF"
BORDER = "#DCE4EE"
TEXT = "#16202C"
DIM = "#5A6B7D"
FAINT = "#8496A8"
ACCENT = "#1F6FEB"

SEV_COLOR = {"临界": "#C1121F", "高": "#CF4A2E", "中": "#9A6700", "提示": "#1F6FEB"}
SEV_BG = {"临界": "#FDECEE", "高": "#FDEEE9", "中": "#FDF6E3", "提示": "#E8F1FE"}


def _score_color(score: int) -> str:
    if score >= 85:
        return "#1A7F37"
    if score >= 60:
        return "#9A6700"
    return "#C1121F"


def _page(title: str, body: str, subtitle: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:{BG}; color:{TEXT};
         font-family:"Microsoft YaHei","Microsoft YaHei UI","Segoe UI",sans-serif;
         font-size:14px; line-height:1.6; }}
  .wrap {{ max-width:1080px; margin:0 auto; padding:28px 24px 60px; }}
  .top {{ display:flex; align-items:flex-start; gap:14px; margin-bottom:20px; }}
  .logo {{ width:40px; height:40px; border-radius:10px; background:{ACCENT};
           color:#fff; font-weight:700; font-size:13px; display:flex;
           align-items:center; justify-content:center; letter-spacing:0.5px;
           font-family:"Microsoft YaHei",sans-serif; }}
  h1 {{ font-size:21px; margin:0 0 4px; font-weight:600; }}
  .sub {{ color:{DIM}; font-size:12.5px; }}
  .card {{ background:{SURFACE}; border:1px solid {BORDER}; border-radius:12px;
           padding:18px 20px; margin-bottom:14px; }}
  .verdict {{ display:flex; gap:20px; align-items:center; }}
  .score {{ width:92px; height:92px; border-radius:50%; flex:0 0 auto;
            display:flex; flex-direction:column; align-items:center;
            justify-content:center; border:6px solid; }}
  .score b {{ font-size:26px; line-height:1; font-family:"Microsoft YaHei",sans-serif; }}
  .score span {{ font-size:11px; color:{DIM}; margin-top:4px; }}
  .kpis {{ display:grid; grid-template-columns:repeat(6,1fr); gap:10px; margin-bottom:14px; }}
  .kpi {{ background:{SURFACE}; border:1px solid {BORDER}; border-radius:10px; padding:11px 13px; }}
  .kpi .l {{ color:{DIM}; font-size:11.5px; }}
  .kpi .v {{ font-size:19px; font-weight:600; font-family:"Microsoft YaHei",sans-serif; margin-top:2px; }}
  h2 {{ font-size:14px; margin:26px 0 10px; font-weight:600; }}
  h2 .bar {{ display:inline-block; width:3px; height:13px; background:{ACCENT};
             border-radius:2px; margin-right:8px; vertical-align:-2px; }}
  .find {{ background:{SURFACE}; border:1px solid {BORDER}; border-left-width:4px;
           border-radius:10px; padding:14px 16px; margin-bottom:10px; }}
  .find .h {{ display:flex; align-items:center; gap:9px; flex-wrap:wrap; }}
  .find .t {{ font-size:14.5px; font-weight:600; }}
  .find .m {{ color:{FAINT}; font-size:11.5px; font-family:"Microsoft YaHei",sans-serif; }}
  .tag {{ font-size:11.5px; padding:1px 8px; border-radius:9px; font-weight:600; }}
  .find p {{ margin:8px 0 0; color:{DIM}; font-size:13px; }}
  .adv {{ margin:10px 0 0; padding:10px 12px; background:#F7FAFE;
          border:1px solid {BORDER}; border-radius:8px; }}
  .adv b {{ font-size:12px; color:{ACCENT}; }}
  .adv ul {{ margin:5px 0 0; padding-left:19px; }}
  .adv li {{ font-size:13px; margin:2px 0; }}
  .ev {{ margin-top:9px; background:#F7F9FC; border-radius:7px; padding:8px 10px;
         font-family:"Microsoft YaHei","Cascadia Mono",monospace; font-size:11.5px;
         color:{FAINT}; white-space:pre-wrap; word-break:break-all; }}
  table {{ width:100%; border-collapse:collapse; font-size:12.5px; background:{SURFACE}; }}
  th {{ text-align:left; background:#F5F8FC; color:{DIM}; font-weight:600;
        font-size:11.5px; padding:8px 10px; border-bottom:1px solid {BORDER};
        position:sticky; top:0; }}
  td {{ padding:7px 10px; border-bottom:1px solid #EFF3F8; vertical-align:top; }}
  tr:nth-child(even) td {{ background:#FBFCFE; }}
  .mono {{ font-family:"Cascadia Mono","Consolas","Microsoft YaHei",monospace; font-size:11.5px; }}
  .lv1 {{ color:#C1121F; font-weight:600; }}
  .lv2 {{ color:#CF4A2E; font-weight:600; }}
  .lv3 {{ color:#9A6700; }}
  .lv4 {{ color:{DIM}; }}
  .kv {{ width:100%; font-size:12.5px; }}
  .kv td {{ padding:4px 12px 4px 0; border:none; }}
  .kv td:first-child {{ color:{DIM}; white-space:nowrap; width:120px; }}
  .foot {{ margin-top:28px; color:{FAINT}; font-size:11.5px; text-align:center; }}
  @media print {{
    body {{ background:#fff; }}
    .card,.find,.kpi,table {{ break-inside:avoid; }}
    .wrap {{ max-width:none; padding:0; }}
  }}
</style></head><body><div class="wrap">
<div class="top">
  <div class="logo">CLS</div>
  <div><h1>{escape(title)}</h1><div class="sub">{subtitle}</div></div>
</div>
{body}
<div class="foot">由 {APP_TITLE} v{VERSION} 生成 · {COMPANY} · {datetime.now():%Y-%m-%d %H:%M:%S}</div>
</div></body></html>"""


def _sev_tag(sev: str) -> str:
    return (f"<span class='tag' style='color:{SEV_COLOR.get(sev, DIM)};"
            f"background:{SEV_BG.get(sev, '#EEF2F7')};'>{escape(sev)}</span>")


def _findings_html(findings) -> str:
    out = []
    for f in findings:
        adv = ""
        if f.advice:
            items = "".join(f"<li>{escape(a)}</li>" for a in f.advice[:8])
            adv = f"<div class='adv'><b>建议动作</b><ul>{items}</ul></div>"
        ev = ""
        if f.evidence:
            ev = f"<div class='ev'>{escape(chr(10).join(f.evidence[:6]))}</div>"
        meta = f"<span class='m'>{escape(f.time_text)}" + \
               (f" · ×{f.count}" if f.count > 1 else "") + "</span>"
        out.append(
            f"<div class='find' style='border-left-color:{SEV_COLOR.get(f.severity, DIM)};'>"
            f"<div class='h'>{_sev_tag(f.severity)}"
            f"<span class='t'>{escape(f.title)}</span>{meta}</div>"
            f"<p>{escape(f.detail)}</p>{adv}{ev}</div>")
    return "".join(out)


# ------------------------------------------------------------------ 日志报告
def export_log_html(path: str, events, analysis, source: str = "") -> str:
    lc = analysis.level_counts
    crashes = len(analysis.crash_times)
    score_color = _score_color(analysis.health)
    kpis = [
        ("事件总数", f"{analysis.total:,}"),
        ("严重", f"{lc.get(1, 0):,}"),
        ("错误", f"{lc.get(2, 0):,}"),
        ("警告", f"{lc.get(3, 0):,}"),
        ("蓝屏记录", f"{crashes}"),
        ("健康评分", f"{analysis.health}"),
    ]
    kpi_html = "".join(
        f"<div class='kpi'><div class='l'>{k}</div><div class='v'>{v}</div></div>"
        for k, v in kpis)

    verdict = f"""
<div class="card"><div class="verdict">
  <div class="score" style="border-color:{score_color};color:{score_color};">
    <b>{analysis.health}</b><span>健康评分</span></div>
  <div>
    <div style="font-size:16px;font-weight:600;margin-bottom:6px;">
      {escape(analysis.headline)}</div>
    <div class="sub">时间跨度：{escape(analysis.span_text)}<br>
      来源：{escape(source or '本机实时通道')}　·　
      事件 {analysis.total:,} 条　·　
      诊断结论 {len(analysis.findings)} 项</div>
  </div>
</div></div>"""

    # 事件明细：优先展示有问题的事件
    keys = sorted(events, key=lambda e: (e.level, e.time or datetime.min))[:400]
    keys.sort(key=lambda e: e.time or datetime.min, reverse=True)
    rows = []
    for e in keys:
        meta = kb.event_meta(e.event_id)
        rows.append(
            f"<tr><td class='mono'>{e.time_text}</td>"
            f"<td class='lv{min(e.level, 4)}'>{e.level_name}</td>"
            f"<td>{escape(e.provider)}</td>"
            f"<td class='mono'>{e.event_id}</td>"
            f"<td>{escape(meta.name_cn if meta else '')}</td>"
            f"<td>{escape(e.summary[:220])}</td></tr>")
    table = ("<table><thead><tr><th style='width:150px'>时间</th><th style='width:58px'>级别</th>"
             "<th style='width:170px'>来源</th><th style='width:64px'>ID</th>"
             "<th style='width:190px'>含义</th><th>摘要</th></tr></thead><tbody>"
             + "".join(rows) + "</tbody></table>")
    note = ""
    if analysis.total > len(keys):
        note = (f"<p class='sub' style='margin-top:8px'>"
                f"明细仅列出最需要关注的 {len(keys)} 条（按级别与时间排序），"
                f"完整 {analysis.total:,} 条见 Excel 导出。</p>")

    body = f"""
{verdict}
<div class="kpis">{kpi_html}</div>
<h2><span class="bar"></span>诊断结论与建议</h2>
{_findings_html(analysis.findings) or "<div class='card'>未发现异常模式。</div>"}
<h2><span class="bar"></span>重点事件明细</h2>
{table}{note}
"""
    html = _page("事件日志分析报告", body,
                 f"生成时间 {datetime.now():%Y-%m-%d %H:%M}　·　分析对象 {escape(source or '本机')}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def export_log_xlsx(path: str, events, analysis, source: str = "") -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="1F6FEB")
    head_font = Font(color="FFFFFF", bold=True, size=10, name="微软雅黑")
    body_font = Font(size=10, name="微软雅黑")
    mono_font = Font(size=10, name="Microsoft YaHei")
    thin = Side(style="thin", color="DCE4EE")
    border = Border(bottom=thin)
    sev_fill = {
        "临界": PatternFill("solid", fgColor="FDECEE"),
        "高": PatternFill("solid", fgColor="FDEEE9"),
        "中": PatternFill("solid", fgColor="FDF6E3"),
        "提示": PatternFill("solid", fgColor="E8F1FE"),
    }

    def style_header(ws, widths):
        for i, w in enumerate(widths, 1):
            cell = ws.cell(row=1, column=i)
            cell.fill = head_fill
            cell.font = head_font
            cell.alignment = Alignment(vertical="center")
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.row_dimensions[1].height = 22
        ws.freeze_panes = "A2"
        if ws.max_row > 1:
            ws.auto_filter.ref = f"A1:{get_column_letter(len(widths))}{ws.max_row}"

    # 结论
    ws = wb.active
    ws.title = "诊断结论"
    heads = ["等级", "类别", "结论", "说明", "次数", "首次时间", "最近时间", "建议动作"]
    ws.append(heads)
    for f in analysis.findings:
        ws.append([f.severity, f.topic, f.title, f.detail, f.count,
                   f.first.strftime("%Y-%m-%d %H:%M:%S") if f.first else "",
                   f.last.strftime("%Y-%m-%d %H:%M:%S") if f.last else "",
                   "；".join(f.advice)])
    style_header(ws, [8, 10, 46, 62, 7, 19, 19, 60])
    for r in range(2, ws.max_row + 1):
        ws.cell(row=r, column=1).fill = sev_fill.get(ws.cell(row=r, column=1).value, sev_fill["提示"])
        for c in range(1, 9):
            cell = ws.cell(row=r, column=c)
            cell.font = body_font
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=c in (3, 4, 8))

    # 明细
    ws2 = wb.create_sheet("事件明细")
    ws2.append(["时间", "级别", "来源", "事件ID", "含义", "通道", "记录ID", "计算机",
                "用户", "进程ID", "线程ID", "摘要", "事件数据"])
    evs = sorted(events, key=lambda e: e.time or datetime.min, reverse=True)
    for e in evs:
        meta = kb.event_meta(e.event_id)
        ws2.append([
            e.time.strftime("%Y-%m-%d %H:%M:%S") if e.time else "",
            e.level_name, e.provider, e.event_id,
            meta.name_cn if meta else "", e.channel, e.record_id,
            e.computer, e.user, e.process_id, e.thread_id,
            e.summary[:900], e.data_text[:900],
        ])
    style_header(ws2, [19, 7, 26, 8, 24, 22, 10, 16, 22, 9, 9, 70, 60])
    for r in range(2, ws2.max_row + 1):
        for c in range(1, 14):
            cell = ws2.cell(row=r, column=c)
            cell.font = mono_font if c in (1, 4, 7, 10, 11) else body_font
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=c in (12, 13))

    # 统计
    ws3 = wb.create_sheet("统计")
    ws3.append(["项目", "数量", "占比"])
    ws3.append(["事件总数", analysis.total, "100%"])
    for lv, name in ((1, "严重"), (2, "错误"), (3, "警告"), (4, "信息"), (5, "详细")):
        n = analysis.level_counts.get(lv, 0)
        ws3.append([f"级别：{name}", n, f"{n / max(analysis.total, 1):.1%}"])
    ws3.append([])
    ws3.append(["按来源 Top 15", "", ""])
    for prov, n in Counter(e.provider for e in events).most_common(15):
        ws3.append([f"  {prov}", n, f"{n / max(analysis.total, 1):.1%}"])
    ws3.append([])
    ws3.append(["按事件 ID Top 15", "", ""])
    for (eid, prov), n in Counter((e.event_id, e.provider) for e in events).most_common(15):
        meta = kb.event_meta(eid)
        label = f"  {eid} · {meta.name_cn if meta else prov}"
        ws3.append([label, n, f"{n / max(analysis.total, 1):.1%}"])
    style_header(ws3, [46, 12, 10])

    wb.properties.creator = COMPANY
    wb.properties.title = "事件日志分析明细"
    wb.save(path)
    return path


# ------------------------------------------------------------------ 转储报告
def export_dump_html(path: str, rep, dana, cdb_text: str = "", cdb=None) -> str:
    score_color = _score_color(dana.health)
    sys_rows = [
        ("文件", rep.name),
        ("类型", rep.kind_text),
        ("大小", rep.size_text),
        ("生成时间", rep.created_text),
        ("操作系统", f"{rep.system.os_name}（{rep.system.version_text}）"),
        ("处理器架构", rep.system.arch_name),
        ("逻辑处理器", str(rep.system.cpus or "-")),
    ]
    if rep.kind == "minidump":
        sys_rows += [
            ("进程 PID", str(rep.process_id or "-")),
            ("线程数", str(rep.thread_count or "-")),
            ("模块数", str(len(rep.modules))),
        ]
    kv = "".join(f"<tr><td>{escape(k)}</td><td>{escape(str(v))}</td></tr>"
                 for k, v in sys_rows)

    mod_rows = "".join(
        f"<tr><td class='mono'>{m.base_text}</td><td>{escape(m.short_name)}</td>"
        f"<td class='mono'>{m.version or '-'}</td><td class='mono'>{m.size_text}</td>"
        f"<td class='mono' style='color:{FAINT}'>{escape(m.name)}</td></tr>"
        for m in sorted(rep.modules, key=lambda x: x.base)[:400])

    body = f"""
<div class="card"><div class="verdict">
  <div class="score" style="border-color:{score_color};color:{score_color};">
    <b>{dana.health}</b><span>健康评分</span></div>
  <div><div style="font-size:16px;font-weight:600;margin-bottom:6px;">
      {escape(dana.headline or rep.key_summary)}</div>
    <div class="sub">{escape(rep.name)}　·　{escape(rep.kind_text)}　·　{escape(rep.size_text)}</div>
  </div>
</div></div>

<h2><span class="bar"></span>文件与系统信息</h2>
<div class="card"><table class="kv">{kv}</table></div>

<h2><span class="bar"></span>诊断结论与建议</h2>
{_findings_html(dana.findings) or "<div class='card'>未识别到崩溃点。</div>"}
"""
    if mod_rows:
        body += f"""
<h2><span class="bar"></span>加载模块（前 400 个）</h2>
<table><thead><tr><th style='width:180px'>基址</th><th style='width:220px'>模块</th>
<th style='width:110px'>版本</th><th style='width:90px'>大小</th><th>完整路径</th></tr></thead>
<tbody>{mod_rows}</tbody></table>
"""
    if cdb is not None and getattr(cdb, "ok", False):
        keys = dict(getattr(cdb, "keys", {}) or {})
        order = ["BUGCHECK_CODE", "EXCEPTION_CODE", "PROCESS_NAME", "MODULE_NAME",
                 "IMAGE_NAME", "IMAGE_VERSION", "SYMBOL_NAME", "FAILURE_BUCKET_ID",
                 "PRIMARY_PROBLEM_CLASS", "OS_VERSION"]
        rows = [(k, keys[k]) for k in order if k in keys]
        if getattr(cdb, "blamed", ""):
            rows.insert(0, ("Probably caused by", cdb.blamed))
        kv2 = "".join(f"<tr><td>{escape(k)}</td><td class='mono'>{escape(str(v))}</td></tr>"
                      for k, v in rows)
        stack = list(getattr(cdb, "stack", []) or [])[:40]
        stack_html = ""
        if stack:
            stack_html = ("<div class='ev' style='margin-top:10px;'>"
                          + escape("\n".join(stack)) + "</div>")
        body += f"""
<h2><span class="bar"></span>调试器结论（cdb !analyze -v）</h2>
<div class="card"><table class="kv">{kv2 or "<tr><td>未识别到 KEY_VALUES</td></tr>"}</table>
{stack_html}</div>
"""
    if cdb_text:
        body += f"""
<h2><span class="bar"></span>调试器原始输出</h2>
<div class="card"><pre class="mono" style="white-space:pre-wrap;margin:0;">{escape(cdb_text[:20000])}</pre></div>
"""
    html = _page("崩溃转储分析报告", body,
                 f"转储文件 {escape(rep.name)}　·　生成时间 {datetime.now():%Y-%m-%d %H:%M}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def export_dump_xlsx(path: str, rep, dana, cdb=None) -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="1F6FEB")
    head_font = Font(color="FFFFFF", bold=True, size=10, name="微软雅黑")
    body_font = Font(size=10, name="微软雅黑")

    ws = wb.active
    ws.title = "诊断结论"
    ws.append(["等级", "类别", "结论", "说明", "建议动作"])
    for f in dana.findings:
        ws.append([f.severity, f.topic, f.title, f.detail, "；".join(f.advice)])
    for i, w in enumerate([8, 12, 50, 70, 70], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for c in range(1, 6):
        ws.cell(row=1, column=c).fill = head_fill
        ws.cell(row=1, column=c).font = head_font
    for r in range(2, ws.max_row + 1):
        for c in range(1, 6):
            ws.cell(row=r, column=c).font = body_font
            ws.cell(row=r, column=c).alignment = Alignment(vertical="top", wrap_text=c >= 3)

    ws2 = wb.create_sheet("模块清单")
    ws2.append(["基址", "模块名", "版本", "大小(KB)", "完整路径"])
    for m in sorted(rep.modules, key=lambda x: x.base):
        ws2.append([m.base_text, m.short_name, m.version, round(m.size / 1024),
                    m.name])
    for i, w in enumerate([20, 30, 16, 12, 90], 1):
        ws2.column_dimensions[get_column_letter(i)].width = w
    for c in range(1, 6):
        ws2.cell(row=1, column=c).fill = head_fill
        ws2.cell(row=1, column=c).font = head_font
    ws2.freeze_panes = "A2"

    ws3 = wb.create_sheet("系统信息")
    ws3.append(["项目", "值"])
    ws3.append(["文件", rep.name])
    ws3.append(["类型", rep.kind_text])
    ws3.append(["大小", rep.size_text])
    ws3.append(["生成时间", rep.created_text])
    ws3.append(["操作系统", rep.system.os_name])
    ws3.append(["版本", rep.system.version_text])
    ws3.append(["架构", rep.system.arch_name])
    ws3.append(["逻辑处理器", rep.system.cpus])
    if rep.bugcheck.code:
        ws3.append(["蓝屏码", rep.bugcheck.code_text])
        for i, p in enumerate(rep.bugcheck.params, 1):
            ws3.append([f"参数{i}", f"0x{p:X}"])
    if rep.exception_code:
        ws3.append(["异常码", rep.exception_text])
        ws3.append(["异常地址", f"0x{rep.exception_address:X}"])
    for row in ws3.iter_rows(min_row=1, max_row=1):
        for c in row:
            c.fill = head_fill
            c.font = head_font
    ws3.column_dimensions["A"].width = 20
    ws3.column_dimensions["B"].width = 60

    if cdb is not None and getattr(cdb, "ok", False):
        ws4 = wb.create_sheet("调试器结论")
        ws4.append(["项目", "值"])
        for k, v in (getattr(cdb, "keys", {}) or {}).items():
            ws4.append([k, v])
        if getattr(cdb, "blamed", ""):
            ws4.append(["Probably caused by", cdb.blamed])
        stack = list(getattr(cdb, "stack", []) or [])[:80]
        if stack:
            ws4.append([])
            ws4.append(["调用栈", ""])
            for line in stack:
                ws4.append([line, ""])
        for c in range(1, 3):
            ws4.cell(row=1, column=c).fill = head_fill
            ws4.cell(row=1, column=c).font = head_font
        ws4.column_dimensions["A"].width = 46
        ws4.column_dimensions["B"].width = 90

    wb.properties.creator = COMPANY
    wb.save(path)
    return path


# ------------------------------------------------------------------ 体检报告
# 体检用 crit/warn/info/ok 四档，日志报告用「临界/高/中/提示」——
# 两套词表混用会让「同一个严重程度出现两种颜色」，这里给体检单独一套，
# 中文一律用 health.LEVEL_CN 的「严重/注意/提示/正常」。
H_SEV_COLOR = {"crit": "#C1121F", "warn": "#9A6700",
               "info": "#1F6FEB", "ok": "#1A7F37"}
H_SEV_BG = {"crit": "#FDECEE", "warn": "#FDF6E3",
            "info": "#E8F1FE", "ok": "#E6F4EA"}
LEVEL_CN_LOCAL = {"crit": "严重", "warn": "注意", "info": "提示", "ok": "正常"}


def _h_tag(sev: str) -> str:
    return (f"<span class='tag' style='color:{H_SEV_COLOR.get(sev, DIM)};"
            f"background:{H_SEV_BG.get(sev, '#EEF2F7')};'>"
            f"{escape(LEVEL_CN_LOCAL.get(sev, sev))}</span>")


def _h_items_html(items, empty: str = "未发现异常。") -> str:
    """体检结论卡。`health.Item` 没有 Finding 的 evidence / 次数，字段更少。"""
    if not items:
        return f"<div class='card' style='color:{DIM}'>{escape(empty)}</div>"
    out = []
    order = {"crit": 0, "warn": 1, "info": 2, "ok": 3}
    for it in sorted(items, key=lambda i: order.get(i.severity, 9)):
        tag = _h_tag(it.severity)
        adv = ""
        if it.advice:
            lis = "".join(f"<li>{escape(a)}</li>" for a in it.advice[:8])
            adv = f"<div class='adv'><b>建议动作</b><ul>{lis}</ul></div>"
        body = f"<p>{escape(it.detail)}</p>" if it.detail else ""
        out.append(
            f"<div class='find' style='border-left-color:"
            f"{H_SEV_COLOR.get(it.severity, DIM)};'>"
            f"<div class='h'>{tag}<span class='t'>{escape(it.title)}</span></div>"
            f"{body}{adv}</div>")
    return "".join(out)


def export_health_html(path: str, rep) -> str:
    """全面体检报告：一份能直接发给客户的单文件 HTML。"""
    color = _score_color(rep.score)
    kpis = "".join(
        f"<div class='kpi'><div class='l'>{escape(s.title)}</div>"
        f"<div class='v' style='color:"
        f"{H_SEV_COLOR.get(s.level, TEXT)};'>"
        f"{escape(LEVEL_CN_LOCAL.get(s.level, s.level))}</div></div>"
        for s in rep.sections)

    verdict = f"""
<div class="card"><div class="verdict">
  <div class="score" style="border-color:{color};color:{color};">
    <b>{rep.score}</b><span>健康评分</span></div>
  <div>
    <div style="font-size:16px;font-weight:600;margin-bottom:6px;">
      {escape(rep.headline)}</div>
    <div class="sub">检查 {len(rep.sections)} 项　·　
      严重 {rep.crit} 项　·　注意 {rep.warn} 项　·　
      耗时 {rep.cost_ms / 1000:.1f} 秒{ '　·　快速模式（未跑 DISM）' if rep.fast else ''}</div>
  </div>
</div></div>"""

    blocks = []
    for s in rep.sections:
        sec_color = H_SEV_COLOR.get(s.level, DIM)
        counts = "　".join(f"{k} {v}" for k, v in (s.counts or {}).items())
        note = ""
        if s.error:
            note = (f"<p style='color:{H_SEV_COLOR['crit']};margin:6px 0 0'>"
                    f"本项未完成：{escape(s.error)}</p>")
        blocks.append(f"""
<h2><span class="bar"></span>{escape(s.title)}
  <span class="tag" style="color:{sec_color};background:#EEF2F7;margin-left:8px;">
    {escape(LEVEL_CN_LOCAL.get(s.level, s.level))}</span></h2>
<div class="card" style="padding:14px 16px;">
  <div style="font-size:14.5px;font-weight:600;">{escape(s.headline or '—')}</div>
  <div class="sub" style="margin-top:4px;">{escape(counts)}</div>
  {note}
</div>
{_h_items_html(s.items)}""")

    # 建议汇总：同一台机器上「该干什么」应当能一眼看完
    seen, todo = set(), []
    for it in rep.problems():
        for a in it.advice:
            if a not in seen:
                seen.add(a)
                todo.append(a)
    todo_html = ("<ul style='margin:0;padding-left:19px'>"
                 + "".join(f"<li style='margin:3px 0'>{escape(a)}</li>"
                           for a in todo[:20]) + "</ul>") if todo else \
                "<div style='color:%s'>没有需要立即处理的动作。</div>" % DIM

    body = f"""
{verdict}
<div class="kpis" style="grid-template-columns:repeat(9,1fr);">{kpis}</div>
<h2><span class="bar"></span>优先处理</h2>
<div class="card">{todo_html}</div>
{''.join(blocks)}
"""
    html = _page("系统体检报告", body,
                 f"生成时间 {datetime.now():%Y-%m-%d %H:%M}　·　"
                 f"全部检查只读取现状，未修改任何系统设置")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def export_health_xlsx(path: str, rep) -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="1F6FEB")
    head_font = Font(color="FFFFFF", bold=True, size=10, name="微软雅黑")
    body_font = Font(size=10, name="微软雅黑")
    thin = Side(style="thin", color="DCE4EE")
    border = Border(bottom=thin)
    # openpyxl 的 fgColor 要 "RRGGBB"，前面带 "＃" 会抛 ValueError
    sev_fill = {LEVEL_CN_LOCAL[k]: PatternFill("solid", fgColor=v.lstrip("#"))
                for k, v in H_SEV_BG.items()}

    def style_header(ws, widths):
        for i, w in enumerate(widths, 1):
            cell = ws.cell(row=1, column=i)
            cell.fill = head_fill
            cell.font = head_font
            cell.alignment = Alignment(vertical="center")
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        if ws.max_row > 1:
            ws.auto_filter.ref = f"A1:{get_column_letter(len(widths))}{ws.max_row}"

    ws = wb.active
    ws.title = "体检结论"
    ws.append(["板块", "等级", "结论", "严重", "注意", "耗时(ms)", "备注"])
    for s in rep.sections:
        ws.append([s.title, LEVEL_CN_LOCAL.get(s.level, s.level), s.headline,
                   s.crit, s.warn, s.cost_ms, s.error])
    style_header(ws, [18, 8, 52, 7, 7, 11, 40])
    for r in range(2, ws.max_row + 1):
        lv = ws.cell(row=r, column=2).value
        ws.cell(row=r, column=2).fill = sev_fill.get(lv, sev_fill["提示"])
        for c in range(1, 8):
            cell = ws.cell(row=r, column=c)
            cell.font = body_font
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=c in (3, 7))

    ws2 = wb.create_sheet("问题清单")
    ws2.append(["等级", "板块", "结论", "说明", "建议动作"])
    order = {"crit": 0, "warn": 1, "info": 2, "ok": 3}
    rows = [(s, it) for s in rep.sections for it in
            sorted(s.items, key=lambda i: order.get(i.severity, 9))]
    for s, it in rows:
        ws2.append([LEVEL_CN_LOCAL.get(it.severity, it.severity), s.title,
                    it.title, it.detail, "；".join(it.advice)])
    style_header(ws2, [8, 18, 46, 62, 56])
    for r in range(2, ws2.max_row + 1):
        lv = ws2.cell(row=r, column=1).value
        ws2.cell(row=r, column=1).fill = sev_fill.get(lv, sev_fill["提示"])
        for c in range(1, 6):
            cell = ws2.cell(row=r, column=c)
            cell.font = body_font
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=c in (3, 4, 5))

    ws3 = wb.create_sheet("指标")
    ws3.append(["板块", "指标", "数值"])
    for s in rep.sections:
        for k, v in (s.counts or {}).items():
            ws3.append([s.title, k, str(v)])
    style_header(ws3, [18, 20, 44])
    for r in range(2, ws3.max_row + 1):
        for c in range(1, 4):
            cell = ws3.cell(row=r, column=c)
            cell.font = body_font
            cell.border = border

    wb.properties.creator = COMPANY
    wb.properties.title = "系统体检报告"
    wb.save(path)
    return path
