# 系统万能工具箱 SystemToolbox

一个 exe 覆盖 Windows 现场运维的九类事：**看日志、看蓝屏、清垃圾、治残留、做优化、查网络、做体检、测 U 盘、查开机**，并且把结论直接说出来。

- **日志分析**：原生 `wevtapi` 读实时通道与离线 `.evtx`，关联分析出「蓝屏 / 掉电 / 磁盘 / 硬件 / 服务 / 应用」结论
- **蓝屏转储**：纯本地解析 MDMP 与内核转储，给出蓝屏码、参数、异常码、故障模块与模块内偏移；可选 cdb 符号增强。
  **一个按钮读出本机全部蓝屏代码**：不用先找文件 —— 自动扫 Minidump / MEMORY.DMP / LiveKernelReports
  的转储头，再和事件日志的 `BugCheck 1001`、`Kernel-Power 41` 按时间对齐，
  把「哪几次真蓝屏、几次只是掉电、哪次挂起后硬关机」列成一张表（全机扫描 87 ms）
- **清理优化**：白名单式垃圾扫描（默认进回收站）、大文件检索、目录占用下钻、磁盘优化与 TRIM
- **残留治理**：注册表孤立引用的硬证据检测（自动 `.reg` 备份）、卸载残留置信度识别、
  **文件占用排查**（Restart Manager 报出「谁占着这个文件」的 PID 与程序名，8~70 ms；
  整目录查有 12 秒预算，超时给出已确认部分而不是干等两分钟）
- **系统优化**：系统信息与硬件概况、启动项启停（可复活）、服务白名单调优、18 项响应速度微调（逐项可还原）、
  **变更记录与一键还原**（把三份备份折成一条时间线）
  —— 整套按 **Windows 10 / Windows 11 双分支**裁剪：不属于本机的项灰显且不可勾选
- **网络诊断**：网卡地址、ICMP 自检、外网与网关、DNS 解析、监听端口、代理与 hosts，
  按层排查「连不上」到底卡在哪一层（全只读）；同一页签里还有**宽带测速**
  —— 下载 / 上传 / 延迟 / 抖动 + 实时速度曲线，并直接说出「这是几兆的宽带、跑到套餐上限了没有」
- **系统体检**：设备问题码与驱动清单、Win11 升级条件逐项判定、CPU/内存/磁盘/网络实时曲线
  + **进程级占用 TOP**（CPU/私有工作集/句柄/线程）、安全基线只读检查
  （Defender / 防火墙 / UAC / SMBv1 / RDP / 自动登录 …）、
  **系统文件与组件健康**（DISM CheckHealth + CBS.log 解析 + 待重启事务 + 补丁级别）、
  **磁盘 SMART**（NVMe 剩余寿命/温度/累计读写/通电时长 + 磁盘 IO 异常）、
  **硬件错误聚合**（WHEA CPER 按严重级别与部件归类，排除错误源通知）、
  **崩溃转储体检**（转储类型 × 页面文件容量核对，回答「蓝屏了为什么没有 dump」）、
  **环境变量体检**（逐条核对 PATH 是否失效 / 重复 / 变量未定义，本机一查就是 8 项失效）、
  **驱动体检**（装了哪些第三方驱动包 · 谁签的 · 哪天从哪个目录装的 · 540 个驱动文件逐个验签
  · 此刻哪些在内核里跑；签名并行 8 线程，冷扫 2.2 秒、热扫 0.35 秒）
  —— 九项全部跑完会给出**整机结论**（健康评分 + 一句话诊断 + 建议动作汇总），
  可一键导出**单文件 HTML 报告**或**三表 Excel**，直接交给客户；
  `SystemToolbox.exe --scan --json` 同一套判断，供 IT 批量采集
- **U 盘测速**：绕过系统缓存的顺序读写、4K 随机读、接口判定（能识别「USB 3.x 盘插在 2.0 口」）、
  同盘历史对比（写入掉速 30% 以上提示老化）；只创建自己的临时文件，同名即放弃、测完必删
- **开机诊断**：读 Windows 自己量的开机耗时（`Diagnostics-Performance` 通道，每次开机自动记录），
  把 `BootTime` 拆成「进系统前 / 桌面出来后」，再拆成**八个阶段**（引导加载、内核、驱动与设备、
  预取与自检、会话与服务、用户配置与桌面、登录后后台、Windows 未细分的残差），
  **跟本机自己的基线比**而不是比固定阈值；慢项按「重复出现」排序点名到具体应用 / 驱动 /
  服务 / 设备；关机与睡眠恢复一并给。全程只读，不改电源计划、不关启动项
- 导出单文件 HTML 现场报告与 Excel 明细；**每张表都能右键复制 / 导出 CSV**
- **四十三张表都能点表头排序**（数字按数值、带单位按实际大小、版本号按段比，
  空值排最后，右键可「恢复原顺序」）；行数多的表（设备 354 行、IO 告警、硬件错误、
  驱动包）上方带**实时筛选框**，勾选状态跟着行走，排序筛选都不会错位
- **运行时不弹任何模态窗口**：完成、失败、警告都是页面顶部一条提示（几秒后自己消失），
  危险操作是页面里的一条红色确认，点了才继续 —— 没有系统对话框那种浅色白板打断

界面：深色取证台单一皮肤（v2.0.0 移除浅色），导航选中块按弹簧物理滑行、切页淡入，并跟随系统「显示动画」开关自动降级。发行：江苏振远智能科技有限公司。

打包：单文件 **23.3 MB**，冷启动到首帧约 **1.3 秒** —— 九个工作台按需构建，PDH 磁盘计数器在后台预热。

动效：导航选中块是**一个**跟着走的块（阻尼比 0.86、角频率 22，350 ms 落定、超调 0.5% 即 1.6 px），
连续点选会从当前位置拐弯而不是排队重放；切页只做 150 ms 透明度柔化（从 0.35 起，不从 0 起）。
系统里关掉「显示动画」时所有时长归零。

视觉刻度：圆角只有四档（卡片 10 / 表格 8 / 控件 6 / 微件 4），嵌套时内层必须比外层小；
KPI 的数字是 22 px、单位降到 13 px 且用次要色 —— 两个都 22 px 时单位会跟数字抢视线。
卡片不用投影，用「上边亮一档、下边暗一档」的 1 px 边缘做浮起感：
8 张卡片的页面挂真投影，一次重绘要从 2.9 ms 涨到 15.9 ms，不值。

偏好只写一个文件：`%LOCALAPPDATA%\SystemToolbox\ui.json`（只存窗口位置）。
不碰注册表 —— 这个工具是便携型 exe，用户期望拷走就干净。
读失败一律当「没设置过」，绝不因为偏好文件坏了打不开。

![日志分析](docs/02_日志分析_深色.png)
![本机蓝屏代码](docs/39_蓝屏转储_本机蓝屏码_深色.png)
![垃圾清理](docs/06_清理优化_垃圾清理_深色.png)
![系统信息](docs/12_系统优化_系统信息_深色.png)
![服务调优](docs/14_系统优化_服务_深色.png)
![网络诊断](docs/16_网络诊断_深色.png)
![设备与驱动](docs/19_系统体检_设备与驱动_深色.png)
![性能采样](docs/21_系统体检_性能采样_深色.png)
![系统文件](docs/23_系统体检_系统文件_深色.png)
![磁盘 SMART](docs/24_系统体检_磁盘SMART_深色.png)
![硬件错误](docs/25_系统体检_硬件错误_深色.png)
![崩溃转储体检](docs/30_系统体检_崩溃转储_深色.png)
![环境变量体检](docs/33_系统体检_环境变量_深色.png)
![驱动体检](docs/34_系统体检_驱动体检_深色.png)
![驱动文件签名](docs/35_系统体检_驱动文件_深色.png)
![文件占用](docs/32_残留治理_文件占用_深色.png)
![页内提示](docs/36_页内提示_完成与跳过.png)
![页内确认](docs/37_页内确认_清理前.png)
![页内确认·永久删除](docs/38_页内确认_永久删除.png)
![U 盘测速](docs/27_U盘测速_结果_深色.png)
![宽带测速](docs/28_网络诊断_宽带测速_深色.png)
![开机诊断](docs/42_开机诊断_概况_深色.png)
![开机慢项排行](docs/43_开机诊断_慢项排行_深色.png)

导航选中块是按弹簧物理滑过去的（不是各自闪背景），连点会从当前位置拐弯：

![导航滑动](docs/29_导航滑动动效.gif)

---

## 快速使用

```bat
:: 直接运行
dist\SystemToolbox.exe

:: 带文件启动
dist\SystemToolbox.exe "D:\现场\System.evtx"
dist\SystemToolbox.exe "D:\现场\MEMORY.DMP"

:: 自检（结果写到 %TEMP%\cls_selftest.txt）
dist\SystemToolbox.exe --selftest
```

快捷键：`Ctrl+1~9` 切换工作台 · `F5` 重新读取日志。

读 `C:\Windows\Minidump`、`MEMORY.DMP`、安全日志，以及清理系统缓存、
整理磁盘、修复系统级注册表项，都需要**以管理员身份运行**
（清理优化 › 磁盘优化 页右上角有「以管理员身份重启」按钮）。

详细操作见 [使用手册.md](使用手册.md)，版本记录见 [CHANGELOG.md](CHANGELOG.md)。

---

## 安全边界

清理与残留治理这两块直接动文件和注册表，所以把边界写死在代码里：

| 环节 | 约束 |
|---|---|
| 垃圾扫描范围 | 只认规则表里的固定路径，且必须落在 `%SystemRoot%` / `%LOCALAPPDATA%` / `%APPDATA%` / `%ProgramData%` 之下；单元测试强制校验，桌面/文档/下载不可能被写进规则 |
| 扫描 | 全程只读，不移动、不删除 |
| 删除 | 默认 `SHFileOperation` 送**回收站**（可还原）；被占用的文件自动跳过并计数 |
| 注册表 | 只报「登记路径确实不存在」的孤立引用；不碰 `SYSTEM` / `Services` / `Winsock` / 安全策略；修复前自动导出 `.reg`，导出失败就中止 |
| 卸载残留 | 启发式结论，按置信度排序、写明理由、**默认全不勾选**，必须人工确认 |
| 磁盘优化 | 只调系统自带 `defrag` / `chkdsk`，不自己搬数据 |
| 启动项 | 只切**启用状态**、不删本体：注册表走 `StartupApproved`（任务管理器同款）、
启动文件夹移入备份区、计划任务 `/disable`；每次动作写 `startup_actions.jsonl` |
| 服务 | 白名单式给建议，**核心服务连按钮都不给**；首次改动记原始启动类型，可「恢复原状」 |
| 响应微调 | 每项只改一个值；改前把「原值 + 本来是否存在」写进 `tweaks.json`，还原以它为准；不适用的项（另一代 Windows 专属）不写注册表、不给勾选框 |
| 变更记录 | 只展示**本工具记录过**的目标，不猜用户手改的东西；一键还原前二次确认，已在原状态的跳过 |
| 网络诊断 | 全程只读：不改网卡、不重置 Winsock、不动 hosts，只把现状和层级结论摆出来 |
| 宽带测速 | 只收发**随机测试数据**：下载从国内镜像站拉公开文件、上传往 Cloudflare 的黑洞端点发定长字节；不上传任何本机信息；按所选时长自动停（5 秒约 300~650 MB 流量），可随时中止 |
| 系统体检 | 全程只读：不装驱动、不改防火墙 / UAC / SMB / 远程设置；安全项只给「该去哪儿改」 |
| 组件健康 | 只跑 `DISM /CheckHealth`（读缓存）与 `CBS.log` 解析；**不执行** `/RestoreHealth`、`sfc /scannow`、不重装、不重启 |
| 磁盘 SMART | 只读 Storport 健康事件与 System 日志：**不下 SMART 指令、不发 ATA 透传、不唤醒休眠盘**；给寿命与趋势，不提供「修复坏道」之类按钮 |
| 硬件错误 | 只解析已有的 WHEA 记录并归类；不改 BIOS、不动超频/电压、不写任何 MSR；建议只写「该查什么」 |
| 崩溃转储 | 只读 `CrashControl` 键值、页面文件大小与 System 通道事件；**不改启动和恢复设置、不动页面文件**，修复只给路径与做法 |
| 文件占用 | 只向 Restart Manager **查询**登记关系，不关进程、不卸载、不强行删除；查不到的内核态占用单独标注，不硬猜 |
| 环境变量 | 只读注册表 `Environment` 与进程环境块；**不改 PATH、不写用户变量**，建议里只给「哪儿改、删哪条」 |
| 开机诊断 | 只读 `Diagnostics-Performance/Operational` 通道与一个注册表值（`HiberbootEnabled`，用来判断是不是「快速启动」口径）；**不改电源计划、不关启动项、不动注册表**，建议只写「该去哪儿关什么」；非管理员时明确说「需要提权」，不假装「没有记录」 |
| 驱动体检 | 只读 `DriverDatabase`、`EnumDeviceDrivers` 与文件签名；**不装驱动、不卸载设备、不改服务启动类型**；签名结论缓存 143 KB 在 `%LOCALAPPDATA%\SystemToolbox\`，文件变了自动失效 |
| 签名校验 | 只做本地 `WinVerifyTrust` + 目录签名比对，`WTD_CACHE_ONLY_URL_RETRIEVAL` 不联网拉吊销列表；结论里不承诺吊销状态 |

---

## 目录结构

```
CrashLogSuite/
├─ main.py                  入口（支持文件参数 / --selftest / --version）
├─ build.spec               PyInstaller 单文件打包配置
├─ app/
│  ├─ theme.py              设计令牌 + QSS + QPalette（单一深色皮肤）
│  ├─ core/
│  │  ├─ evtx.py            wevtapi 读取引擎（实时通道 + 离线文件）
│  │  ├─ minidump.py        MINIDUMP / DUMP_HEADER 解析
│  │  ├─ knowledge.py       蓝屏码 61 / 异常码 24 / 事件 ID 70+ 知识库
│  │  ├─ analysis.py        关联规则引擎 + 健康评分 + 转储结论
│  │  ├─ debugger.py        可选 cdb 调用与输出解析
│  │  ├─ report.py          HTML / Excel 报告导出
│  │  ├─ cleaner.py         垃圾规则表 / 扫描 / 回收站删除
│  │  ├─ disk.py            卷与介质检测 / defrag·chkdsk 调用 / 物理盘健康
│  │  ├─ space.py           大文件检索 + 目录占用统计
│  │  ├─ registry_clean.py  注册表孤立引用检测 / .reg 备份与删除
│  │  ├─ residue.py         卸载残留置信度模型
│  │  ├─ winver.py          Win10 / Win11 分支判定（只认 build 号）
│  │  ├─ sysinfo.py         系统 / 硬件 / 进程 / 安全状态采集（只读）
│  │  ├─ startup.py         启动项三来源扫描与可逆启停
│  │  ├─ services.py        服务扫描、四档判定、启动类型修改与还原
│  │  ├─ tweaks.py          响应微调清单（声明式，逐项可还原）
│  │  ├─ changes.py         变更记录时间线（三份备份折叠）+ 一键还原
│  │  ├─ netdiag.py         网络诊断（网卡/ICMP/网关/DNS/端口/代理/hosts，全只读）
│  │  ├─ netspeed.py        宽带测速（多节点 Range 分段下载 / CF 上传 / TCP 延迟抖动 / 分级口径）
│  │  ├─ devices.py         设备树 + 问题码（cfgmgr32）+ 驱动清单
│  │  ├─ upgrade.py         Win11 升级体检（CPU 代际 / TPM / UEFI / 安全启动）
│  │  ├─ perf.py            CPU/内存/磁盘/网络采样（GetSystemTimes / PDH / GetIfTable）
│  │  ├─ procs.py           进程级采样（CPU 差分 / 私有工作集 / 句柄 / 线程）
│  │  ├─ component.py       组件健康（DISM CheckHealth / CBS.log / 待重启 / 补丁）
│  │  ├─ smart.py           磁盘 SMART（Storport 健康事件 → NVMe 健康日志 + IO 异常）
│  │  ├─ hwevent.py         硬件错误聚合（WHEA CPER 解析 / Section GUID / 内存诊断）
│  │  ├─ dumpcfg.py         崩溃转储体检（转储类型 × 页面文件容量 / 蓝屏与掉电区分）
│  │  ├─ bugchecks.py       本机蓝屏记录（转储头 0x38 取码 + 事件日志对齐 · 零等待全机扫描）
│  │  ├─ locks.py           文件占用排查（Restart Manager / 二分定位 / 内核独占识别）
│  │  ├─ pathenv.py         环境变量体检（PATH 失效·重复·变量未定义）
│  │  ├─ drivers.py        驱动体检（包数据库·签名并行校验·已加载模块）
│  │  ├─ boot.py          开机与登录诊断（Diagnostics-Performance 事件 100/101~110/200~203/300~303 · 八阶段拆解 · 相对基线判定）
│  │  ├─ settings.py      偏好存储（窗口位置，写 LOCALAPPDATA 的单个 JSON）
│  │  ├─ sign.py            Authenticode 校验（内嵌签名 + 目录签名补判）
│  │  ├─ security.py        安全基线只读检查（10 项）
│  │  └─ usbperf.py         U 盘测速（NO_BUFFERING 读写 / 分级阈值 / 历史对比）
│  └─ ui/
│     ├─ main_window.py     主窗口、导航 rail、全局拖放
│     ├─ motion.py          流体交互层（弹簧曲线 / 可打断滑动 / 页面淡入 / 跟随系统动画开关）
│     ├─ usb_page.py        U 盘测速工作台（盘选择 / 分项结果 / 结论 / 历史）
│     ├─ boot_page.py       开机诊断工作台（堆叠柱状图 / 五 KPI / 四页签：时间花在哪·慢项排行·开机历史·关机与睡眠）
│     ├─ log_page.py        日志工作台（KPI / 虚拟表格 / 诊断卡 / 详情）
│     ├─ dump_page.py       转储工作台（拖放 / KPI / 模块 / 原始字段 / cdb / 本机蓝屏记录）
│     ├─ clean_page.py      清理优化（垃圾 / 大文件 / 大目录 / 磁盘优化）
│     ├─ space_page.py      大文件清理 + 大目录分析
│     ├─ residue_page.py    残留治理（注册表冗余 / 卸载残留 / 文件占用）
│     ├─ optimize_page.py   系统优化（系统信息 / 启动项 / 服务 / 响应微调 / 变更记录）
│     ├─ net_page.py        网络诊断（KPI / 检查结论 / 网卡·端口·hosts·宽带测速 四页签）
│     ├─ health_page.py     系统体检（设备驱动 · 升级条件 · 性能曲线+进程 TOP · 安全基线 · 系统文件 · 磁盘 SMART · 硬件错误 · 崩溃转储 · 环境变量 · 驱动体检）
│     ├─ notice.py          页内提示与页内确认（替代 QMessageBox，运行时零弹窗）
│     ├─ tables.py          通用勾选表格模型 + 可停止后台线程 + 复制/导出 CSV
│     └─ widgets.py         图标、KPI 卡、徽标、诊断卡、拖放区、容量条
├─ tests/
│  ├─ test_core.py          30 项单元测试（日志/转储/报告/XML 美化）
│  ├─ test_clean.py         25 项单元测试（规则安全性/扫描/删除/磁盘/注册表）
│  ├─ test_optimize.py      53 项单元测试（命令行解析/StartupApproved/服务判定/微调回滚/版本分支）
│  ├─ test_changes.py       15 项单元测试（时间线解析/去重/统计口径）
│  ├─ test_netdiag.py       22 项单元测试（速度文案/虚拟网卡/主网卡选择/hosts 定性）
│  ├─ test_health.py        31 项单元测试（INF 字符串/问题码判定/CPU 代际/采样/基线打分）
│  ├─ test_v160.py          31 项单元测试（进程排序与差分口径/CBS 噪音分级/KB 正则/WinSxS 中断）
│  ├─ test_v170.py          56 项单元测试（NVMe 单位换算/Critical Warning 位/CPER 偏移校验/GUID 顺序/provider 联动）
│  ├─ test_v180.py          17 项单元测试（懒建页/只建当前页/单皮肤且无切换入口/打包过滤）
│  ├─ test_v190.py          34 项单元测试（NO_BUFFERING 对齐/写读比分级/历史对比/盘符规范化）
│  ├─ test_v1100.py         29 项单元测试（测速分级边界/2.5G 网卡不误报/稳态剔除慢启动/计数器原子性）
│  ├─ test_v1110.py         12 项单元测试（弹簧超调上限/及时起步/无二次回摆/可打断/关动效降级）
│  ├─ test_v1120.py         19 项单元测试（圆角层级/数值·单位拆分/卡片边缘明暗方向/文字对比度）
│  ├─ test_v1130.py         61 项单元测试（转储类型 × 页面文件 5×N 组合判定）
│  ├─ test_v1140.py         64 项单元测试（签名结果码映射/AppStatus 位掩码/PATH 重复口径/RM 预算与分块）
│  ├─ test_v1150.py         35 项单元测试（驱动包识别/已加载判定/签名缓存/结论分级）
│  ├─ test_v1160.py         14 项单元测试（偏好文件容错/原子写/几何串校验）
│  ├─ test_bugchecks.py     42 项单元测试（转储头 0x38 vs 0x400 / 64 位与 32 位 / 事件合并窗口与顺序无关性）
│  ├─ test_v200.py          51 项单元测试（体检编排 fold / 结论等级与评分 / 报告导出 / CLI 控制台编码）
│  ├─ test_tables_v2.py     34 项单元测试（排序键数值/单位/版本/空值 · 勾选按对象身份 · 筛选与排序共存 · 表头挂载）
│  ├─ test_version_v2.py     9 项单元测试（版本号唯一出处 · exe 版本资源读取）
│  ├─ test_v210.py          62 项单元测试（八段口径 / 相对基线 / 单次慢项不判严重 / 无管理员口径 / 关机器睡眠）
│  ├─ boot_fixtures.py      开机事件 XML 构造器（真机字段顺序 + 15/5 字段双模板）
│  ├─ test_ui_smoke.py      13 项界面冒烟（全部工作台离屏建页 / 单皮肤断言 / 结论条初始隐藏）
│  ├─ gen_fixtures.py       合成转储生成器（x64/x86 内核 + 用户模式）
│  ├─ ui_smoke.py           离屏渲染冒烟 + 截图（旧脚本，不进 discover）
│  └─ fixtures/             合成转储样本
├─ tools/
│  ├─ make_icon.py          应用图标生成
│  ├─ probe_ui.py           四工作台离屏/真窗渲染探针
│  ├─ probe_data.py         灌真实扫描数据后截图
│  ├─ probe_clean.py        线程内回收站删除 + defrag 真实输出验证
│  ├─ probe_clean2.py       UI 层完整清理链路验证
│  ├─ probe_opt.py          系统优化四页签冒烟与截图
│  ├─ probe_changes.py      变更记录只读探针（明细 + 统计）
│  ├─ probe_net.py          网络诊断核心探针（网卡/ICMP/DNS/端口/hosts）
│  ├─ probe_net_ui.py       第六工作台渲染探针（页签 + KPI）
│  ├─ probe_devices.py      设备与驱动核心探针（问题码 / 驱动合并 / 统计）
│  ├─ probe_upgrade.py      Win11 升级判定 + CPU 代际解析
│  ├─ probe_health.py       性能采样 + 安全基线
│  ├─ probe_health_ui.py    第七工作台渲染探针（四页签 + 曲线 + Ctrl+7）
│  ├─ probe_usb_ui.py       第八工作台探针（空态 / 真实测速 / 浅色重建）
│  ├─ probe_netspeed_ui.py  宽带测速页签探针（空态 / 真跑 5 秒 / 浅色重建）
│  ├─ probe_apple.py        流体参数的物理实测（弹簧超调/淡入成本/惯性滚动取舍）
│  ├─ probe_apple_ui.py     导航滑动曲线 + 淡入截图
│  ├─ probe_motion_stress.py 连切 24 次 / 动画中关窗
│  ├─ probe_motion_gif.py   把导航滑动录成 GIF（静态图看不出运动）
│  ├─ probe_visual.py       视觉层实测（数字是否等宽 / 阴影开销 / 字距影响）
│  ├─ probe_usb_result.py   回放真实测速记录重拍 U 盘结果页（盘不在手边时用）
│  ├─ probe_boot.py         第九工作台探针（构造样本 → 堆叠图 / 八行阶段表 / 溢出检查 / 四张截图）
│  └─ gen_docs.py           生成 docs/ 界面截图
├─ samples/                 样例报告（真实日志 + 合成转储）
├─ docs/                    界面截图
└─ dist/SystemToolbox.exe   发布产物（单文件，免安装）
```

## 开发

```bat
:: 测试（759 项）
python -m unittest discover -s tests -v

:: 离屏 UI 冒烟并出截图
set QT_QPA_PLATFORM=offscreen && python tests/ui_smoke.py

:: 带真实数据渲染六个工作台
python tools/probe_data.py

:: 网络诊断核心 / 界面 / 变更记录
python tools/probe_net.py && python tools/probe_net_ui.py && python tools/probe_changes.py

:: 系统体检五块
python tools/probe_devices.py && python tools/probe_upgrade.py
python tools/probe_health.py && python tools/probe_health_ui.py

:: 开机诊断（构造样本，不需要管理员）
python tools/probe_boot.py

:: 进程采样 / 组件健康
python tools/probe_v160.py && python tools/probe_v160b.py

:: 磁盘 SMART / 硬件错误聚合
python tools/probe_v170.py && python tools/probe_v170d.py

:: 重新生成测试转储
python tests/gen_fixtures.py tests\fixtures

:: 打包
python -m PyInstaller --noconfirm --workpath build_work --distpath dist build.spec
```

依赖：Python 3.13、PyQt5 5.15、openpyxl 3.1、PyInstaller 6.19（打包用）。
运行期不需要任何第三方运行时。

## 技术要点

| 关注点 | 做法 |
|---|---|
| 读日志不依赖外部进程 | ctypes 直调 `wevtapi.dll`，`EvtQueryFilePath` 处理离线文件 |
| 大日志不卡界面 | 读取放 QThread，表格走 QAbstractTableModel 虚拟化，过滤在内存里做 |
| 大转储不爆内存 | `mmap` + 只读文件头，内核转储不解析物理页 |
| 消息解析不重复 | provider 级 + 事件特征级二级缓存 |
| 皮肤不留残色 | QSS 全由令牌生成 + 配套 QPalette，启动挂一次，没有运行期改样式的路径 |
| 结论可验证 | 每条 finding 带 evidence 行与事件 ID，可一键定位到表内证据 |
| 删文件可回滚 | `SHFileOperationW` + `FOF_ALLOWUNDO` 批量送回收站，删后逐个 `exists` 复核 |
| SSD / HDD 判定 | `IOCTL_STORAGE_QUERY_PROPERTY` 的 SeekPenalty，与 Windows 自身一致 |
| 注册表可回滚 | `reg.exe export` 合成单个 `.reg`，失败即中止，不删无备份的键 |
| 目录扫描不跑飞 | 遍历量上限 + reparse point 跳过 + 可随时停止 |
| 服务不依赖外部命令 | `advapi32` 直调，句柄按 `wintypes.HANDLE` 声明（不声明会在 x64 上被截断而崩溃）|
| 服务显示名可读 | `DisplayName` 是 `@dll,-id` 形式时用 `SHLoadIndirectString` 解析 |
| 硬件事实先探针再写码 | 磁盘 SMART 先试了 5 个 PropertyId × 2 种偏移 × 2 种打开方式全失败，才改用 Storport 事件；SATA 透传因本机无盘可测而**不写** |
| 二进制格式要交叉校验 | CPER 的头部偏移按实测读，并要求 `RecordLength` 等于事件 `Length`，对不上就降级为「格式未识别」 |
| 日志判定要连 Provider | `(provider, event_id)` 联合判定：ID 7 在 `disk` 下是坏块，在 Hyper-V 下是网卡初始化（本机被误报过 17 条） |
| 噪声先分级再统计 | WHEA 的「错误源通知」、CBS 的 `0x800f0805`、CBS 的 Warning 级都先排除，否则健康机器一进页面就是一片红 |
| NVMe 口径 | `Percentage Used` 是已用寿命；`Data Units` 是 `1000 × 512` 字节而不是 `512`；温度字段 0 表示不支持（不能算成 -273℃） |
| 计划任务扫描快 | 直接读 `System32\Tasks` 的 XML，不用 `schtasks /query`（快 5 倍且不受命令行拦截）|
| 启动项可复活 | 写 `StartupApproved`（与任务管理器同机制），不是删除 Run 值 |
| 网络诊断不调外部命令 | 不用 `ping` / `nslookup` / `netstat`：受管控机器上可能被安全策略拦，输出还是本地化文本。全部走 `iphlpapi` + `socket` |
| ICMP 报错看得懂 | `IcmpSendEcho` 的失败原因要 `WinDLL(use_last_error=True)` 才拿得到，否则一律是 `error 0` |
| 进程 CPU 与任务管理器同口径 | 分母是整机容量（`GetSystemTimes` 的 `内核+用户`，内核时间已含空闲），整机满载 = 100%；除以核数会变成「满一核 = 100%」，单核进程显示 100% 会误导 |
| 进程采集够快 | `Toolhelp32` + `QueryFullProcessImageNameW` 一轮 190 进程约 24 ms，可每秒采；句柄类入参必须显式声明 `HANDLE`，否则 x64 上被截断 |
| CBS.log 不能按关键字判错 | `Failed to ... 0x800f0805` 是每次枚举包都写的常态。按「日志级别字段 + HRESULT 白/黑名单」判，再把 Info 级无关行整体忽略 |
| 慢检查不放进自动流程 | WinSxS 体积（11 万文件 / 15 GB）做成按需按钮 + `stop_flag` 可中断 + 结果缓存；「进页面卡一分钟」比没有更糟 |
| 网关不通不误报 | 先测外网再测网关：路由器普遍默认禁 ping，外网通就不算故障 |
| 网卡状态判对 | `dwOperStatus` 是 `INTERNAL_IF_OPER_STATUS`，只有 `>= 4` 才算连通（`==1` 是「不可达」） |
| hosts 不误判 | `0.0.0.0` 屏蔽是广告过滤的正常写法，判「劫持」要同时满足「常见域名」+「指向具体公网 IP」 |
| 设备名不是文件名 | `DeviceDesc` 是 INF 字符串引用 `@usb.inf,%token%;文本`，解析时优先取分号后的回退文本，再查 INF 的 `[Strings]` |
| 问题码只能问 CM | 注册表里没有设备问题码（`CSConfigFlags` 已废弃），必须 `CM_Get_DevNode_Status` |
| 无问题码即正常 | `Motherboard resources` 这类资源节点不报 `DN_STARTED`，拿它当故障会一次误报上百台设备 |
| 图表刻度不裁字 | Y 轴文字宽度用 `QFontMetrics` 实测算内边距，按字符数估算会把 `5000 KB/s` 裁成 `30 KB/s` |
| 数字别换字体 | 微软雅黑的数字本来就是等宽的（12 px 每字 7 px、22 px 每字 13 px，'111' 与 '888' 同宽）—— 为了对齐去换等宽字体是白改，实测过才省下这次返工 |
| 大号数字的字距是伪优化 | 22 px 上加 -0.3 字距，'894.2' 只从 62 px 收到 61 px；真正拉开层次的是**给单位降级**（22 → 13 px + 次要色），不是挤字距 |
| 卡片浮起不用投影 | `QGraphicsDropShadowEffect` 会让卡片走离屏渲染：8 张卡片实测 2.9 ms → 15.9 ms。改用上下 1 px 色差（`border-top-color` / `border-bottom-color`），成本为 0 |
| 浅色的小字要压深 | 浅色 `faint` 原为 #8496A8，白底上只有 3.0:1，11 px 看着发灰；压到 #738499 得 3.8:1，仍属「次要」但能读清 |
| 动效参数靠实测定 | 弹簧不是调 `QEasingCurve` 凑出来的：ζ=0.9/ω=18 超调只有 0.2%，但实测前 50 ms 只走 7.8%，点下去像没反应；改成 ζ=0.86/ω=22 后前 50 ms 走到 22%，超调仍只有 0.5%（1.6 px） |
| 动画别用 QVariantAnimation 起步 | 实测它 `start()` 后头两帧不推进（36 ms 内位置不动），正好吃掉「点了有没有反应」那段时间；改 QTimer + `perf_counter` 真实时间，首帧延迟降到 12 ms |
| 页面淡入不做位移 | 叠位移要改 layout，真实页面（300 行表格）单帧 24.4 ms，41 fps；纯透明度 180 ms 实测 184 ms，几乎零成本 —— 只做透明度 |
| 淡入不从 0 起 | 从全透明起步时切换后头 45 ms 截图是一片空白，感知是「卡了半拍」；从 0.35 起内容立刻就在，只留明暗柔化 |
| 挂着透明度效果会拖慢滚动 | `QGraphicsOpacityEffect` 会让控件走离屏渲染，所以动画一结束立刻 `setGraphicsEffect(None)` |
| 动效要能关 | 读 `SPI_GETCLIENTAREAANIMATION`，系统关掉「显示动画」时时长全部归零（晕动症 / 远程桌面 / 录屏的人都靠这个） |
| 事件字段按名字取，不按位置 | `Diagnostics-Performance` 的 `EventData` 里 `Name` 属性是英文的，本地化只影响消息正文 —— 所以 `BootTime` / `MainPathBootTime` 在中文版、英文版机器上是同一个名字。按 `Data[3]` 取位置的话，微软哪天插一个字段就全错位 |
| 开机快慢跟自己的基线比 | 同一台 22 秒正常、另一台 45 秒也正常，固定阈值会把一半正常机器标红。改成「相对本机最近若干次中位数 ≥ 1.6 倍 **且** 多花 ≥ 1.5 秒 **且** 至少 3 次样本」，三个条件缺一不可 |
| 单次异常不许判「严重」 | 一条只出现过一次的 16 秒应用超时，曾把总时长 62 秒（基线 53 秒）的机器判成「严重」。现在 crit 要求「≥ 90 秒」或「≥ 45 秒且存在平均每次多花 ≥ 20 秒的重复项」；判定列按出现次数上色，不按严重程度上色 |
| 残差不假装成 100% | 八段能归因的只占 `MainPathBootTime` 的一部分（实测一台 `MainPath=64858` 有 34 秒在 Session Init 与 Winlogon 等待里），剩下单列「会话与登录（未细分）」并给出查证方向 |
| 查询错误会被「容忍」掉 | `EvtQuery` 带 `EvtQueryTolerateQueryErrors` 时，非管理员读系统通道返回 **0 个事件 + 一个查询错误**，不抛异常。不先查 `is_admin()` 就会显示「还没有开机记录」，把用户引去重启一台没问题的机器 |
| 混用 QPoint / QPointF 会静默崩溃 | `drawLine(QPoint, QPointF)` 落不到任何 C++ 重载上，进程直接消失（退出码 127、无 traceback），try/except 兜不住 —— 只能二分到最小可复现后统一成 `QPointF` |
| 截图必须用真字体引擎 | 本机 `QT_QPA_PLATFORM=offscreen` 没有字体引擎，截出来 567 字节、字全空白。一律 `windows` 平台 + `WA_DontShowOnScreen`（不上屏，但字体是真的） |
| 杀软状态不误判 | `Get-MpComputerStatus` 在装了第三方杀软时报「已关闭」，要再查 SecurityCenter2 是否有人在接管 |

## 许可

内部使用，仅限江苏振远智能科技有限公司售后与研发。
