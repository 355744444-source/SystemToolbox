# -*- coding: utf-8 -*-
"""知识库：蓝屏码 / 异常码 / 事件 ID 语义与排查建议。

这里的内容决定工具的专业度——不是把数字翻译一遍，而是给出
「这个码意味着什么硬件/软件环节出问题 + 现场先查什么」。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BugCheckInfo:
    code: int
    name: str
    name_cn: str
    advice: list[str] = field(default_factory=list)
    culprit: str = ""          # 常见元凶方向
    params_note: list[str] = field(default_factory=list)


def _bc(code, name, cn, culprit, advice, params=None) -> tuple:
    return code, BugCheckInfo(code, name, cn, advice, culprit, params or [])


BUGCHECKS: dict[int, BugCheckInfo] = dict([
    _bc(0x0000000A, "IRQL_NOT_LESS_OR_EQUAL", "内核访问了非法/可分页地址",
        "第三方驱动（网卡、显卡、存储、杀软过滤驱动）",
        ["优先更新或回滚最近安装/升级的驱动，尤其是网卡与存储驱动",
         "若近期装了杀毒、虚拟化、硬件监控类软件，先卸载验证",
         "跑一遍 Windows 内存诊断，排除内存条故障",
         "用驱动验证器 Driver Verifier 定向复现（生产机慎用）"],
        ["参数1=被访问的地址", "参数2=访问时的 IRQL", "参数3=访问类型(0读/1写/8执行)", "参数4=出错指令地址"]),
    _bc(0x0000001E, "KMODE_EXCEPTION_NOT_HANDLED", "内核模式异常未被处理",
        "驱动未捕获异常或内存越界",
        ["看参数4指向的模块，更新该驱动", "检查是否超频/内存不稳定", "关闭 XMP/EXPO 后复测"]),
    _bc(0x0000003B, "SYSTEM_SERVICE_EXCEPTION", "系统服务例程中发生异常",
        "显卡驱动、杀软、虚拟化驱动",
        ["更新显卡驱动（干净安装）", "卸载近期新增的过滤驱动/安全软件", "检查系统文件：sfc /scannow"],
        ["参数1=异常码", "参数2=出错指令地址", "参数3=上下文记录地址"]),
    _bc(0x00000050, "PAGE_FAULT_IN_NONPAGED_AREA", "访问了不存在的非分页内存",
        "内存条故障 / 驱动野指针 / 磁盘页文件损坏",
        ["先跑内存诊断（MemTest 过夜）", "chkdsk /f 检查磁盘", "更新近期变更的驱动"]),
    _bc(0x0000007E, "SYSTEM_THREAD_EXCEPTION_NOT_HANDLED", "系统线程异常未处理（32 位常见）",
        "驱动或系统服务",
        ["参数2 通常是异常码（如 0xC0000005 访问违例）", "定位参数3/4 所在模块并更新"]),
    _bc(0x0000007F, "UNEXPECTED_KERNEL_MODE_TRAP", "内核陷入意外陷阱",
        "内存故障 / CPU 过热或超频 / 驱动栈溢出",
        ["双核/超频场景先恢复默认频率", "检查散热与电源功率", "跑内存诊断"]),
    _bc(0x0000009F, "DRIVER_POWER_STATE_FAILURE", "驱动电源状态切换失败",
        "睡眠/唤醒相关驱动（USB、网卡、蓝牙、存储）",
        ["禁用快速启动与混合睡眠后复测", "更新芯片组与 USB 控制器驱动", "拔掉可疑外设（USB HUB、扩展坞）"]),
    _bc(0x000000D1, "DRIVER_IRQL_NOT_LESS_OR_EQUAL", "驱动在高 IRQL 访问非法地址",
        "网卡/无线/存储驱动",
        ["参数3 读取/写入方向，参数4 出错地址", "更新网卡与芯片组驱动", "关闭网卡节能（EEE/绿色以太网）"]),
    _bc(0x000000C2, "BAD_POOL_CALLER", "驱动错误调用内存池",
        "第三方驱动内存池使用错误",
        ["参数1 的低位表示请求类型", "更新近期驱动；必要时开启 Driver Verifier 定位"]),
    _bc(0x000000C4, "DRIVER_VERIFIER_DETECTED_VIOLATION", "驱动验证器捕获到违规",
        "被验证的驱动本身有缺陷",
        ["这是排查利器：说明已验证目标驱动存在确定性缺陷", "参数1 为违规子类型，查表定位具体规则"]),
    _bc(0x000000C5, "DRIVER_CORRUPTED_EXPOOL", "驱动破坏了内存池结构",
        "野指针写入的驱动",
        ["结合前后事件定位最近更新的驱动", "启用 Driver Verifier 定向验证"]),
    _bc(0x000000EF, "CRITICAL_PROCESS_DIED", "关键系统进程终止",
        "系统文件损坏 / 杀软误杀 / 磁盘故障",
        ["sfc /scannow 与 DISM /RestoreHealth", "检查磁盘 SMART 与坏道", "卸载激进的安全软件"]),
    _bc(0x000000F4, "CRITICAL_OBJECT_TERMINATION", "关键对象被终止",
        "磁盘 I/O 挂起导致 csrss/wininit 无法工作",
        ["重点查存储链路：硬盘、数据线、NVMe 固件", "检查是否有磁盘超时事件（Event 129/153）"]),
    _bc(0x000000ED, "UNMOUNTABLE_BOOT_VOLUME", "引导卷无法挂载",
        "磁盘坏道 / 文件系统损坏 / 数据线接触",
        ["chkdsk /f /r 修复", "更换 SATA 线或 M.2 插槽复测", "检查 SMART 健康度"]),
    _bc(0x0000007B, "INACCESSIBLE_BOOT_DEVICE", "启动设备不可访问",
        "BIOS 存储模式变更（AHCI/RAID/VMD）/ 驱动缺失",
        ["确认 BIOS 中 SATA 模式与装机时一致", "服务器阵列卡驱动是否随镜像注入", "PE 下确认磁盘可见"]),
    _bc(0x00000024, "NTFS_FILE_SYSTEM", "NTFS 文件系统错误",
        "磁盘坏道 / 内存故障 / 非正常断电",
        ["chkdsk /f", "检查内存", "排查掉电与电源质量"]),
    _bc(0x0000009C, "MACHINE_CHECK_EXCEPTION", "CPU 报告机器检查异常",
        "CPU/内存/主板/供电硬件故障",
        ["恢复 BIOS 默认设置，关闭超频", "检查电源与散热", "更新 BIOS 微码"]),
    _bc(0x0000008E, "KERNEL_MODE_EXCEPTION_NOT_HANDLED", "内核模式异常未处理（x86）",
        "驱动",
        ["参数1 为异常码，参数3/4 指向出错模块"]),
    _bc(0x000000BE, "ATTEMPTED_WRITE_TO_READONLY_MEMORY", "向只读内存写入",
        "驱动写常量区",
        ["参数1=目标地址，参数2=PTE，参数3/4=保留", "更新报错模块对应驱动"]),
    # 0xBE 的「新编号」版本。本机 2026-10-20 的 ResourceTimeout 实况报告就是 0x1CC，
    # 所以必须有条目，否则本机唯一一条真实蓝屏码会显示成「知识库未收录」。
    _bc(0x000001CC, "ATTEMPTED_WRITE_TO_READ_ONLY_MEMORY", "试图改动只读内存区",
        "第三方驱动或内核组件越界写入",
        ["按错误信息里的地址找所属驱动，优先更新或回滚该驱动",
         "重点查安全软件的过滤驱动、硬件监控/灯效工具、虚拟化与外设重定向驱动",
         "对可疑驱动开 Driver Verifier（生产机慎用，先在测试机验证）",
         "若同时出现资源超时（ResourceTimeout）报告，多为存储/USB 重定向驱动卡死"]),
    _bc(0x000000A5, "ACPI_BIOS_ERROR", "ACPI BIOS 不兼容或损坏",
        "主板 BIOS / 电源管理实现",
        ["更新 BIOS 到最新", "BIOS 中关闭快速启动、改用 UEFI 模式", "最小化外设后复测"]),
    _bc(0x000000B4, "VIDEO_DRIVER_INIT_FAILURE", "显卡驱动初始化失败",
        "显卡驱动 / 显卡硬件",
        ["安全模式卸载显卡驱动后干净安装", "更换显卡插槽/供电线复测"]),
    _bc(0x000000EA, "THREAD_STUCK_IN_DEVICE_DRIVER", "线程卡死在内核驱动中",
        "显卡驱动挂死",
        ["更新或回滚显卡驱动", "关闭硬件加速/超频测试"]),
    _bc(0x00000101, "CLOCK_WATCHDOG_TIMEOUT", "某 CPU 核心无响应（时钟中断超时）",
        "CPU 供电/超频/散热、虚拟机嵌套虚拟化",
        ["BIOS 恢复默认，关闭超频与 C-State 深度节能", "检查散热与供电", "在虚拟机中运行需关闭嵌套虚拟化"]),
    _bc(0x00000109, "CRITICAL_STRUCTURE_CORRUPTION", "内核关键结构被篡改",
        "驱动篡改内核代码/数据（常见于加壳、外挂、老旧安全软件）",
        ["排查近期安装的底层软件", "开启驱动签名强制", "离线扫描系统文件"]),
    _bc(0x00000116, "VIDEO_TDR_FAILURE", "显卡驱动超时未响应，恢复失败",
        "显卡驱动 / 显卡硬件 / 供电",
        ["干净安装显卡驱动", "降低 GPU 负载与频率复测", "检查显卡供电与温度"]),
    _bc(0x00000117, "VIDEO_TDR_TIMEOUT_DETECTED", "显卡驱动超时未响应",
        "显卡驱动",
        ["更新驱动", "调整 TDR 延迟做验证", "排查显存/GPU 过热"]),
    _bc(0x00000119, "VIDEO_SCHEDULER_INTERNAL_ERROR", "显卡调度器内部错误",
        "显卡驱动",
        ["更新或回滚显卡驱动", "关闭多显卡混插/硬件加速"]),
    _bc(0x0000010E, "VIDEO_MEMORY_MANAGEMENT_INTERNAL", "显存管理内部错误",
        "显卡驱动 / 显存故障",
        ["更新驱动", "降低分辨率与刷新率复测", "检查显卡散热"]),
    _bc(0x00000124, "WHEA_UNCORRECTABLE_ERROR", "硬件不可纠正错误（WHEA）",
        "CPU / 内存 / PCIe 设备 / 主板供电",
        ["这是纯硬件方向：先看 WHEA-Logger 事件里的错误来源（CPU/内存/PCIe）",
         "恢复 BIOS 默认，关闭超频/XMP",
         "重插内存与 PCIe 设备、清理金手指",
         "单条内存逐一排查，必要时更换"],
        ["参数1=错误来源类型(0=机器检查/1=纠正后恢复等)", "参数2=错误状态", "参数3=物理地址", "参数4=保留"]),
    _bc(0x00000133, "DPC_WATCHDOG_VIOLATION", "DPC 看门狗超时",
        "存储驱动（老旧 SSD 固件）、网卡驱动、杀软过滤驱动",
        ["参数1=0 表示单次 DPC 超时；=1 表示累计超时",
         "更新 SSD 固件与存储控制器驱动",
         "检查是否有磁盘超时事件（Event 129/153）",
         "关闭硬盘 LPM 节能"],
        ["参数1=0 单次/1 超时上限", "参数2=超时上限(时钟中断数)", "参数3=已用时间", "参数4=保留"]),
    _bc(0x00000139, "KERNEL_SECURITY_CHECK_FAILURE", "内核安全检查失败",
        "驱动破坏内核对象（链表、栈 Cookie）",
        ["用 Driver Verifier 定向复现", "排查近期更新的驱动与安全软件"]),
    _bc(0x0000013A, "KERNEL_MODE_HEAP_CORRUPTION", "内核堆损坏",
        "驱动堆溢出/UAF",
        ["Driver Verifier 开启 Special Pool 定位", "排查近期驱动变更"]),
    _bc(0x00000154, "UNEXPECTED_STORE_EXCEPTION", "内存压缩存储异常",
        "内存条故障 / 页文件所在磁盘异常",
        ["内存诊断", "检查系统盘健康", "临时关闭内存压缩复测"]),
    _bc(0x00000019, "BAD_POOL_HEADER", "内存池头损坏",
        "两次释放/越界写（UAF）",
        ["Driver Verifier 定位", "排查近期驱动"]),
    _bc(0x0000002E, "DATA_BUS_ERROR", "数据总线错误",
        "内存条/主板/显存硬件故障",
        ["重插或更换内存", "检查主板电容与供电", "跑内存与显卡压力测试"]),
    _bc(0x00000044, "MULTIPLE_IRP_COMPLETE_REQUESTS", "同一 IRP 被重复完成",
        "驱动缺陷",
        ["更新报错模块驱动", "使用 Driver Verifier 复现"]),
    _bc(0x0000004E, "PFN_LIST_CORRUPT", "页帧数据库损坏",
        "内存故障 / 驱动错误操作页表",
        ["内存诊断", "更新驱动", "检查是否超频"]),
    _bc(0x00000051, "REGISTRY_ERROR", "注册表访问错误",
        "磁盘坏道导致注册表 hive 读失败 / 内存故障",
        ["chkdsk 检查磁盘", "检查 SMART", "内存诊断"]),
    _bc(0x00000074, "BAD_SYSTEM_CONFIG_INFO", "系统配置信息损坏",
        "注册表损坏 / 非正常断电",
        ["尝试最后一次正确配置/系统还原", "检查磁盘与内存"]),
    _bc(0x00000077, "KERNEL_STACK_INPAGE_ERROR", "内核栈换页失败",
        "磁盘坏道 / 数据线 / 内存",
        ["chkdsk /r", "更换数据线", "检查 SMART"]),
    _bc(0x0000007A, "KERNEL_DATA_INPAGE_ERROR", "内核数据换页失败",
        "磁盘坏道 / 内存故障 / 页文件损坏",
        ["这是典型磁盘/内存问题：先看参数2 的 NTSTATUS",
         "chkdsk /r 与 SMART 检测",
         "内存诊断",
         "更换数据线或硬盘"]),
    _bc(0x000000CE, "DRIVER_UNLOADED_WITHOUT_CANCELLING_PENDING_OPERATIONS", "驱动卸载时未取消挂起操作",
        "驱动缺陷",
        ["更新或移除该驱动"]),
    _bc(0x000000F7, "DRIVER_OVERRAN_STACK_BUFFER", "驱动栈溢出",
        "驱动缓冲区溢出（常见于加壳/保护驱动）",
        ["更新驱动，排查安全软件"]),
    _bc(0x000000FC, "ATTEMPTED_EXECUTE_OF_NOEXECUTE_MEMORY", "试图执行不可执行内存",
        "驱动跳转到数据区（DEP 拦截）",
        ["更新驱动；排查加壳/保护类软件"]),
    _bc(0x000000E6, "DRIVER_VERIFIER_DMA_VIOLATION", "DMA 验证违规",
        "驱动 DMA 使用错误",
        ["硬件与驱动兼容性排查，更新芯片组驱动"]),
    _bc(0x000000A0, "INTERNAL_POWER_ERROR", "电源策略内部错误",
        "电源管理驱动 / 固件",
        ["更新 BIOS 与芯片组驱动", "关闭快速启动与睡眠复测"]),
    _bc(0x0000009A, "SYSTEM_PTE_MISUSE", "系统页表项误用",
        "驱动错误操作页表",
        ["更新驱动，启用 Driver Verifier"]),
    _bc(0x000000F3, "DISORDERLY_SHUTDOWN", "关机时资源未释放",
        "关机流程被驱动/服务阻塞",
        ["检查关机慢的驱动与服务", "更新主板芯片组驱动"]),
    _bc(0x000000F5, "FLTMGR_FILE_SYSTEM", "文件系统过滤驱动错误",
        "杀软/加密软件/同步盘过滤驱动",
        ["卸载近期安装的安全或加密软件", "更新过滤驱动"]),
    _bc(0x000000B8, "ATTEMPTED_SWITCH_FROM_DPC", "在 DPC 中试图切换线程",
        "驱动违反调度规则",
        ["更新报错驱动"]),
    _bc(0x000000C1, "SPECIAL_POOL_DETECTED_MEMORY_CORRUPTION", "特殊池内存损坏",
        "驱动越界写",
        ["Driver Verifier 定位"]),
    _bc(0x000000C9, "DRIVER_VERIFIER_IOMANAGER_VIOLATION", "I/O 管理器验证违规",
        "驱动 I/O 使用错误", ["参数1 为违规子类型，查表定位"]),
    _bc(0x000000CA, "PNP_DETECTED_FATAL_ERROR", "即插即用致命错误",
        "驱动枚举/释放设备对象错误", ["更新芯片组与相关设备驱动"]),
    _bc(0x000000D5, "DRIVER_PAGE_FAULT_IN_FREED_SPECIAL_POOL", "访问已释放的特殊池",
        "驱动释放后使用（UAF）", ["Driver Verifier 定位"]),
    _bc(0x000000E3, "RESOURCE_NOT_OWNED", "释放了非自己持有的资源",
        "驱动资源管理缺陷", ["更新驱动"]),
    _bc(0x000000F6, "PCI_VERIFIER_DETECTED_VIOLATION", "PCI 验证违规",
        "PCIe 设备驱动/固件", ["更新固件与驱动"]),
    _bc(0x00000080, "NMI_HARDWARE_FAILURE", "NMI 硬件故障",
        "硬件（内存/主板/PCIe）", ["硬件方向排查：内存、主板、插卡"]),
    _bc(0x00000094, "KERNEL_STACK_LOCKED_AT_EXIT", "退出时内核栈仍被锁定",
        "驱动缺陷", ["更新驱动"]),
    _bc(0xC000021A, "STATUS_SYSTEM_PROCESS_TERMINATED", "关键用户态进程终止",
        "winlogon/csrss 被破坏（系统文件损坏或杀软误杀）",
        ["sfc /scannow、DISM 修复", "卸载安全软件后复测"]),
    _bc(0xC0000221, "STATUS_IMAGE_CHECKSUM_MISMATCH", "系统文件校验和不匹配",
        "系统文件损坏 / 内存故障 / 磁盘坏道",
        ["sfc /scannow", "内存诊断", "检查磁盘"]),
    _bc(0xDEADDEAD, "MANUALLY_INITIATED_CRASH1", "人为触发的崩溃",
        "有人通过键盘/调试器强制崩溃",
        ["非故障：检查是否测试或现场误操作触发"]),
])

# 兼容 0x1000007E / 0x1000008E 这类带位标志的写法
def bugcheck_info(code: int) -> Optional[BugCheckInfo]:
    if not code:
        return None
    code &= 0xFFFFFFFF
    for mask in (0x10000000, 0x00000000):
        candidate = code & 0x0FFFFFFF if mask else code
        if candidate in BUGCHECKS:
            return BUGCHECKS[candidate]
    return None


@dataclass
class ExceptionInfo:
    code: int
    name: str
    name_cn: str
    advice: list[str] = field(default_factory=list)


def _ex(code, name, cn, advice) -> tuple:
    return code, ExceptionInfo(code, name, cn, advice)


EXCEPTIONS: dict[int, ExceptionInfo] = dict([
    _ex(0xC0000005, "ACCESS_VIOLATION", "访问违例（读写非法内存）",
        ["参数1=0 读 / 1 写 / 8 执行；参数2 为被访问地址",
         "参数2 为 0 或极小值 → 空指针/未初始化指针",
         "参数2 为 0xFFFFFFFF 类 → 释放后使用（UAF）",
         "用 WinDbg 加载符号看调用栈，定位到具体函数"]),
    _ex(0xC00000FD, "STACK_OVERFLOW", "栈溢出（递归过深或局部变量过大）",
        ["检查递归终止条件", "避免在栈上分配大数组", "增大线程栈作为临时验证"]),
    _ex(0xC0000374, "HEAP_CORRUPTION", "堆损坏（越界写/重复释放）",
        ["报错位置常在 ntdll!RtlReportCriticalFailure，真凶是之前的越界写",
         "开启 PageHeap（gflags /p /enable app.exe /full）复现",
         "重点检查数组下标、memcpy 长度、字符串拼接"]),
    _ex(0xC0000409, "STACK_BUFFER_OVERRUN / FAST_FAIL", "栈缓冲区溢出或被主动快速失败",
        ["参数1 为 FAST_FAIL 子码：常见 2=栈 Cookie 被改，7=致命堆错误",
         "开启 /GS 与 CFG 编译选项", "排查越界写与字符串处理"]),
    _ex(0xC0000094, "INT_DIVIDE_BY_ZERO", "整数除零", ["检查除数为 0 的分支"]),
    _ex(0xC0000095, "INTEGER_OVERFLOW", "整数溢出", ["检查算术边界"]),
    _ex(0xC0000096, "PRIVILEGED_INSTRUCTION", "执行了特权指令", ["检查内联汇编/驱动接口调用"]),
    _ex(0xC000001D, "ILLEGAL_INSTRUCTION", "非法指令", ["多为 CPU 指令集不兼容（如 AVX512 在老 CPU 上）", "检查编译优化选项"]),
    _ex(0xC000008C, "ARRAY_BOUNDS_EXCEEDED", "数组越界", ["检查下标"]),
    _ex(0xC0000006, "IN_PAGE_ERROR", "内存页读取失败", ["磁盘/网络路径不可用或文件被删除", "检查 IO 与权限"]),
    _ex(0xC0000017, "NO_MEMORY", "内存不足", ["检查是否内存泄漏", "降低并发/缓存上限"]),
    _ex(0xC0000018, "CONFLICTING_ADDRESSES", "地址冲突", ["检查 DLL 基址冲突 / ASLR 设置"]),
    _ex(0xC0000135, "DLL_NOT_FOUND", "依赖 DLL 未找到", ["检查运行目录与 PATH", "确认 VC++ 运行库已安装"]),
    _ex(0xC0000139, "ENTRYPOINT_NOT_FOUND", "DLL 入口点不存在（版本不匹配）", ["确认 DLL 与 exe 版本配套"]),
    _ex(0xC0000142, "DLL_INIT_FAILED", "DLL 初始化失败", ["检查 DLL 依赖与权限"]),
    _ex(0xC00002B5, "MULTIPLE_FAULT_VIOLATION", "多重故障", ["先看内存/驱动稳定性"]),
    _ex(0xC0000602, "FAIL_FAST_EXCEPTION", "快速失败（主动崩溃）", ["查看事件日志中的 FastFail 子码与消息"]),
    _ex(0xE06D7363, "MSVC_CPP_EXCEPTION", "C++ 异常（未捕获）",
        ["这是 C++ throw 的异常码，不是内存错误", "定位未捕获的 throw 与 std::terminate 调用"]),
    _ex(0xE0434352, ".NET_CLR_EXCEPTION", ".NET 托管异常", ["查看 .NET Runtime 事件(1026)中的异常类型与堆栈"]),
    _ex(0x40080201, "WINRT_ORIGINATE_ERROR", "WinRT 异步错误", ["检查 WinRT API 返回值"]),
    _ex(0x80000003, "BREAKPOINT", "断点异常", ["通常为调试残留或调试器附加"]),
    _ex(0x80000002, "DATATYPE_MISALIGNMENT", "数据未对齐访问", ["检查结构体对齐与指针强转"]),
    _ex(0x80000004, "SINGLE_STEP", "单步陷阱", ["调试器相关"]),
    _ex(0x406D1388, "MS_VC_EXCEPTION(ThreadName)", "线程命名消息（非故障）", ["该异常被调试器用于设置线程名，可忽略"]),
])


def exception_info(code: int) -> Optional[ExceptionInfo]:
    return EXCEPTIONS.get(code & 0xFFFFFFFF)


# --------------------------------------------------------------- 事件 ID 语义
@dataclass
class EventMeta:
    event_id: int
    name_cn: str
    severity: str = "提示"       # 临界 / 高 / 中 / 提示
    topic: str = ""              # 蓝屏 / 掉电 / 磁盘 / 硬件 / 服务 / 应用 / 电源 / 安全
    why: str = ""
    advice: list[str] = field(default_factory=list)


def _em(eid, name, sev, topic, why, advice=()) -> tuple:
    return eid, EventMeta(eid, name, sev, topic, why, list(advice))


EVENT_META: dict[int, EventMeta] = dict([
    _em(41, "系统未正常关机就重启（Kernel-Power）", "高", "掉电",
        "内核在断电/死锁/蓝屏后重新启动，Windows 未走正常关机流程",
        ["若同一时刻有 1001 事件 → 蓝屏，按蓝屏码排查",
         "没有 1001 → 多为直接掉电：查电源、供电线路、主板供电、过温保护",
         "笔记本查电池与适配器"]),
    _em(1001, "蓝屏错误检查（BugCheck）", "临界", "蓝屏",
        "系统写入崩溃转储并记录蓝屏码，这是蓝屏的第一手证据",
        ["用本工具的转储工作台打开 C:\\Windows\\Minidump 下同名 dmp 继续定位"]),
    _em(6008, "上一次关机是意外的", "高", "掉电",
        "事件日志服务记录到上次系统关闭未经正常流程", ["同 41 事件排查方向"]),
    _em(6005, "事件日志服务已启动", "提示", "开关机", "可用于统计开机次数与运行时长"),
    _em(6006, "事件日志服务已停止", "提示", "开关机", "正常关机标志"),
    _em(6013, "系统运行时间统计", "提示", "开关机", "记录本次系统已运行天数，可用于判断重启频率"),
    _em(1074, "用户/进程发起的关机或重启", "提示", "开关机",
        "能看出是谁（用户、Windows 更新、安装程序）触发的重启",
        ["排查“莫名重启”时先看这条：可能是更新或第三方程序在重启"]),
    _em(1076, "上一次关机由谁发起", "提示", "开关机", "补充 1074 的关机原因"),
    _em(12, "操作系统启动", "提示", "开关机", "内核启动时间点"),
    _em(13, "操作系统正在关闭", "提示", "开关机", "内核关机时间点"),
    _em(20, "Windows 更新已安装", "提示", "更新", "更新安装可能伴随重启与驱动变更"),
    _em(19, "Windows 更新安装成功", "提示", "更新", "关注更新时间点是否与故障时间吻合"),
    _em(43, "Windows 更新开始安装", "提示", "更新", ""),
    _em(7, "磁盘存在坏块", "高", "磁盘",
        "设备在某扇区重试读写失败，通常意味着物理坏道或闪存单元劣化",
        ["立即备份数据", "查看 SMART（05/C5/C6 计数）", "考虑更换硬盘"]),
    _em(11, "磁盘控制器错误", "高", "磁盘", "控制器报告错误，检查数据线与驱动"),
    _em(51, "分页操作时磁盘错误", "高", "磁盘",
        "页文件读写失败，会直接引发 0x77/0x7A 蓝屏", ["chkdsk /r 与 SMART 检查"]),
    _em(153, "磁盘 IO 操作被重试", "中", "磁盘",
        "IO 超时后重试成功，说明链路不稳（线材/固件/供电）",
        ["更新 SSD 固件", "更换数据线/插槽", "关闭硬盘节能 LPM"]),
    _em(129, "磁盘复位（Reset）", "高", "磁盘",
        "设备被复位，常见于 NVMe 超时或供电不足", ["检查 NVMe 散热与供电", "更新固件"]),
    _em(134, "NVMe 超时", "高", "磁盘", "NVMe 命令超时，固件/散热/兼容性问题"),
    _em(157, "磁盘已被意外移除", "高", "磁盘", "热插拔或接触不良"),
    _em(98, "卷健康状况变化", "中", "磁盘", "卷进入异常状态，需检查文件系统与磁盘"),
    _em(55, "NTFS 文件系统损坏", "高", "磁盘", "文件系统结构损坏，立即 chkdsk /f 并备份"),
    _em(130, "NTFS 事务日志错误", "中", "磁盘", "元数据不一致，chkdsk 修复"),
    _em(17, "已纠正的硬件错误（WHEA）", "中", "硬件",
        "CPU/内存/PCIe 报告了可纠正错误，数量多说明硬件在劣化",
        ["统计条数与来源（Processor/Memory/PCIe）", "关闭超频/XMP，更新 BIOS", "重点关注不可纠正错误(18/19)"]),
    _em(18, "硬件错误（WHEA，不可纠正）", "临界", "硬件",
        "不可纠正硬件错误，通常紧跟 0x124 蓝屏", ["按硬件方向排查：内存、CPU 供电、PCIe 设备"]),
    _em(19, "硬件错误（WHEA，已纠正/已上报）", "高", "硬件", "与 0x124 强相关"),
    _em(46, "WHEA 内存错误", "高", "硬件", "内存条或内存控制器报错", ["单条内存逐一测试", "关闭 XMP/EXPO"]),
    _em(47, "WHEA 内存错误（已纠正）", "中", "硬件", "内存 ECC 纠正记录", ["统计密度，必要时更换内存"]),
    _em(1, "时间同步异常", "提示", "时间", "系统时间跳变会导致日志时间线错乱"),
    _em(50, "时间同步失败", "中", "时间", "NTP 不可达，检查网络与时间源"),
    _em(36, "时间服务配置警告", "提示", "时间", ""),
    _em(1000, "应用程序崩溃", "高", "应用",
        "应用因未处理异常退出，事件里包含故障模块与异常码",
        ["看“错误模块名称”与“异常代码”", "有 dmp 时用转储工作台分析"]),
    _em(1002, "应用程序无响应（挂起）", "中", "应用",
        "界面线程阻塞或无响应超时", ["抓取挂起时的调用栈", "检查同步 IO/死锁"]),
    _em(1026, ".NET 运行时未处理异常", "高", "应用",
        "托管代码异常导致进程退出", ["关注异常类型与堆栈信息"]),
    _em(7031, "服务意外终止（将重启）", "高", "服务", "服务崩溃，Windows 尝试自动恢复"),
    _em(7034, "服务意外终止", "高", "服务", "服务崩溃，结合前后事件定位"),
    _em(7000, "服务启动失败", "中", "服务", "检查服务账户权限与依赖服务"),
    _em(7009, "等待服务连接超时", "中", "服务", "服务启动过慢或被阻塞"),
    _em(7011, "服务响应超时（事务）", "中", "服务", ""),
    _em(7040, "服务启动类型被更改", "提示", "服务", "有人或程序改动了服务配置"),
    _em(7045, "安装了新服务", "中", "服务", "确认是否为授权变更，异常服务需警惕"),
    _em(219, "驱动加载失败", "高", "驱动", "签名/兼容性问题导致驱动未加载"),
    _em(108, "驱动加载超时", "中", "驱动", ""),
    _em(10016, "DCOM 权限不足", "提示", "权限",
        "组件调用权限缺失，绝大多数情况下无害，可忽略"),
    _em(1102, "安全日志被清空", "临界", "安全", "审计日志被清除，需确认是否授权操作"),
    _em(104, "事件日志被清空", "高", "安全", "日志被清除，现场证据可能丢失"),
    _em(4625, "登录失败", "中", "安全", "大量失败可能是暴力破解或凭据过期"),
    _em(4740, "账户被锁定", "中", "安全", "多次失败登录导致锁定"),
    _em(4624, "登录成功", "提示", "安全", ""),
    _em(37, "处理器电源管理（限频/节流）", "中", "电源",
        "CPU 因温度或供电被降频，可解释性能骤降", ["检查散热与供电", "调整电源方案"]),
    _em(2019, "服务器无法分配内存", "中", "资源", "非分页池耗尽"),
    _em(2020, "服务器内存分配失败", "中", "资源", "分页池耗尽"),
    _em(2004, "资源耗尽（服务）", "中", "资源", ""),
])


def event_meta(event_id: int) -> Optional[EventMeta]:
    meta = EVENT_META.get(event_id)
    if meta and not meta.name_cn:
        return None
    return meta


TOPIC_META = {
    "蓝屏": "蓝屏崩溃",
    "掉电": "异常掉电/非正常重启",
    "磁盘": "磁盘与存储",
    "硬件": "硬件错误 (WHEA)",
    "服务": "服务与驱动",
    "应用": "应用崩溃",
    "电源": "电源与散热",
    "安全": "安全审计",
    "更新": "系统更新",
    "开关机": "开关机记录",
    "时间": "时间同步",
    "权限": "组件权限",
    "资源": "系统资源",
    "驱动": "驱动加载",
    "": "其他",
}
