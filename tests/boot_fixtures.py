# -*- coding: utf-8 -*-
"""开机诊断的合成事件。

真机的这个通道要管理员才读得到（`EvtQuery` 带容错后普通用户只会拿到 0 条），
所以在开发机上没法靠「读一遍本机」来验证解析。这里按微软事件清单的字段名
造事件：ID 100 的 44 个字段、101/102/103/109 的 15 字段模板、
104/110 的 5 字段模板，都按官方顺序给全。

字段名以检测维基（detection.wiki/microsoft-windows-diagnostics-performance）
与微软 Q&A 里贴出的真实 XML 为准：`BootTime` / `MainPathBootTime` /
`BootPostBootTime` / `OSLoaderDuration` / `TotalTime` / `DegradationTime` …
少给字段也允许 —— 解析必须容忍缺失（老系统确实少字段）。
"""
from __future__ import annotations

from app.core import boot, evtx

NS = "http://schemas.microsoft.com/win/2004/08/events/event"

# 一次真实开机（微软 Q&A 里贴出来的 307 秒那条，字段与顺序照抄）
BOOT_FIELDS = {
    "BootTsVersion": 2,
    "SystemBootInstance": 14,
    "UserBootInstance": 14,
    "BootKernelInitTime": 53,
    "BootDriverInitTime": 181,
    "BootDevicesInitTime": 546,
    "BootPrefetchInitTime": 0,
    "BootPrefetchBytes": 0,
    "BootAutoChkTime": 0,
    "BootSmssInitTime": 145400,
    "BootCriticalServicesInitTime": 83,
    "BootUserProfileProcessingTime": 482,
    "BootMachineProfileProcessingTime": 20,
    "BootExplorerInitTime": 66525,
    "BootNumStartupApps": 14,
    "BootIsRebootAfterInstall": "false",
    "BootRootCauseStepImprovementBits": 0,
    "BootRootCauseGradualImprovementBits": 0,
    "BootRootCauseStepDegradationBits": 0,
    "BootRootCauseGradualDegradationBits": 0,
    "BootIsDegradation": "false",
    "BootIsStepDegradation": "false",
    "BootIsGradualDegradation": "false",
    "BootImprovementDelta": 0,
    "BootDegradationDelta": 0,
    "BootIsRootCauseIdentified": "false",
    "OSLoaderDuration": 1185,
    "BootPNPInitStartTimeMS": 53,
    "BootPNPInitDuration": 4382,
    "OtherKernelInitDuration": 101,
    "SystemPNPInitStartTimeMS": 4469,
    "SystemPNPInitDuration": 150,
    "SessionInitStartTimeMS": 4634,
    "Session0InitDuration": 940,
    "Session1InitDuration": 179,
    "SessionInitOtherDuration": 144279,
    "WinLogonStartTimeMS": 150034,
    "OtherLogonInitActivityDuration": 530,
    "UserLogonWaitDuration": 2669,
}


def _xml(eid: int, fields: dict, when: str, level: int = 2,
         task: int = 4002) -> str:
    body = "".join('<Data Name="%s">%s</Data>' % (k, v)
                   for k, v in fields.items())
    return (
        '<Event xmlns="%s">'
        '<System><Provider Name="Microsoft-Windows-Diagnostics-Performance"/>'
        '<EventID>%d</EventID><Version>2</Version><Level>%d</Level>'
        '<Task>%d</Task><Opcode>34</Opcode>'
        '<TimeCreated SystemTime="%s"/>'
        '<EventRecordID>1</EventRecordID>'
        '<Channel>Microsoft-Windows-Diagnostics-Performance/Operational</Channel>'
        '<Computer>TEST-PC</Computer>'
        '<Security UserID="S-1-5-19"/></System>'
        '<EventData>%s</EventData></Event>'
    ) % (NS, eid, level, task, when, body)


def boot_xml(boot_ms: int = 307275, main_ms: int = None,
             post_ms: int = None, when: str = "2019-08-29T10:51:02.7079260Z",
             degraded: bool = False, extra: dict = None) -> str:
    """ID 100。`when` 同时用于 BootEndTime（引擎优先用它当记录时间）。

    阶段字段要跟着 `main_ms` 一起缩放 —— 只改总数不改分段，会造出
    「分段合计 217 秒、总开机 62 秒」这种自相矛盾的样本，
    拿它验证拆段等于什么都没验证。
    """
    # 只给总时长时，按样本的 71% / 29% 拆两段 —— 原来固定 217594/89681，
    # 传 boot_ms=62000 时会造出「两段之和 307 秒 > 总时长 62 秒」的矛盾样本，
    # 引擎会按「两段为准」重算总时长，结果测试里的 62 秒被悄悄改成 307 秒
    if main_ms is None and post_ms is None:
        main_ms = int(boot_ms * 0.71)
        post_ms = boot_ms - main_ms
    elif main_ms is None:
        main_ms = max(0, boot_ms - post_ms)
    elif post_ms is None:
        post_ms = max(0, boot_ms - main_ms)
    f = dict(BOOT_FIELDS)
    f["BootStartTime"] = when
    f["BootEndTime"] = when
    f["BootTime"] = boot_ms
    f["MainPathBootTime"] = main_ms
    f["BootPostBootTime"] = post_ms
    f["BootIsDegradation"] = "true" if degraded else "false"
    k = (main_ms / 217594.0) if main_ms else 0.0      # 样本的原始 MainPath 是 217594
    for key in ("BootKernelInitTime", "BootDriverInitTime", "BootDevicesInitTime",
                "BootPrefetchInitTime", "BootAutoChkTime", "BootSmssInitTime",
                "BootCriticalServicesInitTime", "BootUserProfileProcessingTime",
                "BootMachineProfileProcessingTime", "BootExplorerInitTime",
                "BootPNPInitDuration", "OtherKernelInitDuration",
                "SystemPNPInitDuration", "Session0InitDuration",
                "Session1InitDuration", "SessionInitOtherDuration",
                "OtherLogonInitActivityDuration", "UserLogonWaitDuration"):
        f[key] = int(BOOT_FIELDS[key] * k)
    if extra:
        f.update(extra)
    return _xml(100, f, when, level=2 if degraded else 4)


SLOW_FIELDS = {
    "NameLength": 11,
    "FriendlyNameLength": 22,
    "VersionLength": 9,
    "PathLength": 40,
    "ProductNameLength": 20,
    "CompanyNameLength": 22,
}


def slow_xml(eid: int, name: str, total_ms: int, degrade_ms: int,
             when: str = "2019-08-29T10:51:02.7079260Z",
             friendly: str = "", path: str = "", version: str = "",
             company: str = "Microsoft Corporation") -> str:
    """101/102/103/109 同构（15 字段），104~108/110 是 5 字段。"""
    f = dict(SLOW_FIELDS)
    f.update({
        "StartTime": when,
        "Name": name,
        "FriendlyName": friendly,
        "Version": version,
        "TotalTime": total_ms,
        "DegradationTime": degrade_ms,
        "Path": path,
        "ProductName": "Microsoft Malware Protection",
        "CompanyName": company,
    })
    if eid in (104, 105, 106, 107, 108, 110):
        # 阶段类事件没有文件信息，只有名称与两个耗时
        f = {"StartTime": when, "NameLength": len(name), "Name": name,
             "TotalTime": total_ms, "DegradationTime": degrade_ms}
    return _xml(eid, f, when, level=3)


def shutdown_xml(ms: int = 25058, when: str = "2019-08-29T10:45:00Z") -> str:
    f = {
        "ShutdownTsVersion": 1, "ShutdownStartTime": when, "ShutdownEndTime": when,
        "ShutdownTime": ms, "ShutdownUserSessionTime": 2728,
        "ShutdownUserPolicyTime": 37, "ShutdownUserProfilesTime": 84,
        "ShutdownSystemSessionsTime": 20852,
        "ShutdownPreShutdownNotificationsTime": 15732,
        "ShutdownServicesTime": 5007, "ShutdownKernelTime": 1477,
        "ShutdownRootCauseStepImprovementBits": 0,
        "ShutdownRootCauseGradualImprovementBits": 0,
        "ShutdownRootCauseStepDegradationBits": 0,
        "ShutdownRootCauseGradualDegradationBits": 0,
        "ShutdownIsDegradation": "false", "ShutdownTimeChange": 0,
    }
    return _xml(200, f, when, level=3, task=4007)


def sleep_xml(suspend_ms: int = 4000, resume_ms: int = 12000,
              when: str = "2019-08-29T09:00:00Z") -> str:
    f = {
        "StandbyTsVersion": 1, "StandbyAppCount": 0, "StandbyServicesCount": 0,
        "StandbyDevicesCount": 0, "StandbyStartTime": when, "StandbyEndTime": when,
        "StandbySuspendTotal": suspend_ms, "StandbySuspendTotalChange": 0,
        "ResumeStartTime": when, "ResumeEndTime": when,
        "StandbyResumeTotal": resume_ms, "StandbyResumeTotalChange": 0,
        "StandbyIsDegradation": "false", "StandbyIsTroubleshooterLaunched": "false",
        "StandbyIsRootCauseIdentified": "false",
    }
    return _xml(300, f, when, level=4)


def parse(xmls) -> list:
    """XML → 事件对象，丢掉解析失败的（解析器自己会返回 None）。"""
    out = []
    for x in xmls:
        ev = evtx.parse_event_xml(x)
        if ev is not None:
            out.append(ev)
    return out


def make_events(count: int = 5, boot_ms: int = 62000) -> list:
    """一份「有内容」的样本：多次开机 + 重复慢项 + 关机/睡眠。

    最近一次特意做成 post_boot 占大头（启动项在忙），这是现场最常见的一种。
    """
    xmls = []
    for i in range(count):
        slow = 0 if i == 0 else 3000 * i
        xmls.append(boot_xml(
            boot_ms=boot_ms - slow,
            main_ms=int((boot_ms - slow) * 0.62),
            post_ms=int((boot_ms - slow) * 0.38),
            when="2019-08-2%dT10:51:02.7079260Z" % (9 - i % 9),
            degraded=(i == 0 and boot_ms >= 90000)))
    # 同一只杀毒在最近两次开机里都被点名 → 必须被聚合成「重复出现」
    xmls.append(slow_xml(101, "MsMpEng.exe", 20632, 16332,
                         when="2019-08-29T10:51:02.7079260Z",
                         friendly="Antimalware Service Executable",
                         version="3.0.8402.0",
                         path=r"C:\Program Files\Microsoft Security Client\Antimalware\MsMpEng.exe"))
    xmls.append(slow_xml(101, "MsMpEng.exe", 17000, 14000,
                         when="2019-08-28T10:51:02.7079260Z",
                         friendly="Antimalware Service Executable",
                         version="3.0.8402.0",
                         path=r"C:\Program Files\Microsoft Security Client\Antimalware\MsMpEng.exe"))
    # 只出现过一次的服务与驱动：不该被升级成「真问题」
    xmls.append(slow_xml(103, "windefend", 326, 234,
                         when="2019-08-29T10:51:02.7079260Z",
                         friendly="Microsoft Defender 防病毒"))
    xmls.append(slow_xml(102, "nvlddmkm.sys", 8200, 6100,
                         when="2019-08-27T10:51:02.7079260Z",
                         friendly="NVIDIA Windows Kernel Mode Driver",
                         path=r"C:\Windows\System32\drivers\nvlddmkm.sys"))
    xmls.append(slow_xml(110, "SMSSInit", 12000, 9000,
                         when="2019-08-29T10:51:02.7079260Z"))
    xmls.append(shutdown_xml())
    xmls.append(sleep_xml())
    # 关机/睡眠各自拖时间的对象（201 / 301），与 101 同构
    xmls.append(slow_xml(201, "soffice.bin", 18200, 16100,
                         when="2019-08-29T10:45:00Z",
                         friendly="OpenOffice.org 3.3",
                         path=r"C://Program Files (x86)//OpenOffice.org 3//program//soffice.bin"))
    xmls.append(slow_xml(301, "SearchIndexer.exe", 8000, 5200,
                         when="2019-08-29T09:00:00Z",
                         friendly="Windows Search"))
    return parse(xmls)


def make_scan(count: int = 5, boot_ms: int = 62000) -> boot.BootScan:
    return boot.scan(events=make_events(count, boot_ms), with_registry=False)
