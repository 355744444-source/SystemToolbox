# -*- coding: utf-8 -*-
"""宽带测速：下载 / 上传 / 延迟 / 抖动。

为什么不像别的工具那样直接引 `speedtest-cli`：

- 本项目坚持零第三方运行时依赖（打包已经压到 23 MB，不想为一个功能涨回去）；
- `speedtest-cli` 依赖 Ookla 的服务器列表接口，国内经常拿不到。

所以这里直接开几条 HTTP 连接，从**国内镜像站**拉大文件（`Range` 分段、
读到 EOF 再从头循环、收到的数据直接丢弃），上传走 Cloudflare 的 `__up` 端点
（本机实测可用的公共上传黑洞）。

## 口径（全部本机实测，不是拍脑袋）

| 场景 | 实测 | 结论 |
|---|---|---|
| 下载 1 线程 | 173 Mbps | 单连接吃不满千兆 |
| 下载 2 线程 | 344 Mbps | |
| 下载 4 线程 | 599 Mbps | 单节点基准 |
| 下载 8 线程 | 589 Mbps | 同一节点再加线程反而降，还多了一堆 416 |
| **下载 2 节点 × 4 线程** | **874 / 891 Mbps** | **采用这个** |
| 上传 1 线程 | 27 Mbps | |
| 上传 3 线程 | 104 Mbps | **固定 3 线程** |
| 上传 6 线程 | 103 Mbps | 已经饱和 |

三个必须记住的坑：

1. **必须丢掉前 1 秒**。TCP 慢启动 + TLS 握手全落在头 1 秒里，
   不剔除的话同一台机器能测出 159 Mbps（含启动）和 173 Mbps（稳态）两种结果。
2. **镜像站之间差两个数量级，而且单个节点自己也在跳**。
   同一时刻同一台机器：腾讯 308、华为 162、阿里云 5 Mbps；
   再测一轮腾讯 836 / 华为 618，逐秒看华为会掉到 364。
   所以「自动」= 先给每个节点各拉 0.8 秒挑出前两名，再让它们**各跑 4 条连接**。
   两轮实测：单节点 809~836，双节点 874~891，逐秒波动也从 ±300 收到 ±80。
3. **`Range` 起点越过文件尾会回 416**。所以读到 EOF 就回 0，
   遇到 416 也回 0 重来，绝不当成故障。
"""
from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass, field

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SystemToolbox"
READ = 128 * 1024            # 单次 read 大小；64K 时 GIL 切换太频繁
WARMUP = 1.0                 # 剔除的慢启动窗口（秒）
TICK = 0.25                  # 采样间隔
DL_THREADS = 4               # 每个节点的并发连接数
MAX_NODES = 2                # 同时压几个节点（1 个节点 8 线反而更慢）
UP_THREADS = 3
UP_BODY = 4 * 1024 * 1024    # 每次 POST 4 MB


@dataclass(frozen=True)
class Node:
    key: str
    name: str
    url: str
    host: str
    where: str = ""


# `ubuntu/ls-lR.gz` 是 Ubuntu 官方镜像脚本生成的归档清单，约 38 MB，
# 三大国内镜像同一路径，常年存在、支持 Range —— 比写死某个 ISO 版本稳。
NODES = (
    Node("tencent", "腾讯云镜像", "https://mirrors.cloud.tencent.com/ubuntu/ls-lR.gz",
         "mirrors.cloud.tencent.com", "国内"),
    Node("huawei", "华为云镜像", "https://mirrors.huaweicloud.com/ubuntu/ls-lR.gz",
         "mirrors.huaweicloud.com", "国内"),
    Node("aliyun", "阿里云镜像", "https://mirrors.aliyun.com/ubuntu/ls-lR.gz",
         "mirrors.aliyun.com", "国内"),
)

UPLOAD_URL = "https://speed.cloudflare.com/__up"
UPLOAD_HOST = "speed.cloudflare.com"
UPLOAD_NAME = "Cloudflare"

SIZES = (5, 8, 12)           # 可选测试时长（秒）


def node_of(key: str) -> Node | None:
    for n in NODES:
        if n.key == key:
            return n
    return None


# ------------------------------------------------------------------ 网络小件
# `urllib.request` 冷导入要 190 ms（连带 http.client / email），
# 而它只在真正测速时才用得上 —— 放模块顶层会让整个工具箱的启动白等 0.2 秒。
# 所以这里延迟到函数里 import（首次 ~190 ms，之后只是 sys.modules 查表）。
_CTX: list = []
_INSECURE: list = []


def _ctx():
    import ssl
    if not _CTX:
        _CTX.append(ssl.create_default_context())
    return _CTX[0]


def _ctx_loose():
    import ssl
    if len(_CTX) < 2:
        c = ssl.create_default_context()
        c.check_hostname = False
        c.verify_mode = ssl.CERT_NONE
        _CTX.append(c)
    return _CTX[-1]


def insecure_used() -> bool:
    return bool(_INSECURE)


def proxy_note() -> str:
    """本次请求会走的代理地址（环境变量或 IE 设置），空表示直连。

    有代理时测的是「整条链路」的速度，不是纯宽带 —— 企业网关 / VPN 客户端
    都会改这个，结论里必须说清楚，否则用户会拿代理的速度去投诉运营商。
    """
    try:
        import urllib.request
        p = urllib.request.getproxies()
    except Exception:                   # noqa: BLE001
        return ""
    for key in ("https", "http"):
        if p.get(key):
            return str(p[key])
    return ""


def _req(url: str, rng: str, data):
    import urllib.request
    hdr = {"User-Agent": UA, "Accept-Encoding": "identity"}
    if rng:
        hdr["Range"] = f"bytes={rng}"
    if data is not None:
        hdr["Content-Type"] = "application/octet-stream"
    return urllib.request.Request(
        url, data=data, headers=hdr, method="POST" if data is not None else "GET")


def _open(url: str, timeout: float = 8.0, rng: str = "", data=None):
    """统一入口：先带证书校验，证书类错误再降级重试一次（记一笔）。"""
    import ssl
    import urllib.request
    try:
        return urllib.request.urlopen(_req(url, rng, data), timeout=timeout,
                                      context=_ctx())
    except Exception as exc:                    # noqa: BLE001
        # **不能只 catch ssl.SSLError**：http.client 把 TLS 错误包成
        # URLError(reason=SSLError) 再抛（SSLError 本身是 OSError 的子类），
        # 只判 ssl.SSLError 会让这条降级路径永远走不到。
        reason = getattr(exc, "reason", exc)
        if not isinstance(reason, ssl.SSLError):
            raise
        _INSECURE.append(True)
        return urllib.request.urlopen(_req(url, rng, data), timeout=timeout,
                                      context=_ctx_loose())


class _Counter:
    """多线程共享的字节计数器。CPython 里 `+=` 不是原子的，加锁。"""

    __slots__ = ("n", "lock")

    def __init__(self) -> None:
        self.n = 0
        self.lock = threading.Lock()

    def add(self, k: int) -> None:
        with self.lock:
            self.n += k

    def get(self) -> int:
        with self.lock:
            return self.n


# ------------------------------------------------------------------ 节点探测
@dataclass
class Probe:
    node: Node
    mbps: float = 0.0
    ttfb_ms: float = 0.0
    err: str = ""

    @property
    def ok(self) -> bool:
        return not self.err and self.mbps > 0


def _probe_one(node: Node, seconds: float) -> Probe:
    """单连接拉一小段，算真实吞吐 —— 比 TTFB 更能区分节点好坏。"""
    p = Probe(node)
    counter = _Counter()
    r = None
    try:
        t0 = time.perf_counter()
        r = _open(node.url, timeout=6.0, rng="0-")
        p.ttfb_ms = (time.perf_counter() - t0) * 1000
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            b = r.read(READ)
            if not b:
                break
            counter.add(len(b))
    except Exception as exc:            # noqa: BLE001
        p.err = f"{type(exc).__name__}: {exc}"
        return p
    finally:
        # read() 超时/断流会直接跳进 except，原来那句 r.close() 就执行不到，
        # socket 挂着直到 GC —— 一轮 3 个节点、反复测速会攒住连接。
        if r is not None:
            try:
                r.close()
            except Exception:           # noqa: BLE001
                pass
    dt = max(0.05, time.perf_counter() - t0)
    p.mbps = counter.get() * 8 / dt / 1e6
    return p


def probe_nodes(seconds: float = 0.8, nodes: tuple = NODES,
                stop_flag=None) -> list[Probe]:
    """并行探测所有节点，按吞吐从高到低返回。"""
    out: list[Probe] = [Probe(n) for n in nodes]

    def one(i: int) -> None:
        if stop_flag and stop_flag():
            out[i] = Probe(nodes[i], err="已取消")
            return
        out[i] = _probe_one(nodes[i], seconds)

    ts = [threading.Thread(target=one, args=(i,), daemon=True)
          for i in range(len(nodes))]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=seconds + 6)
    return sorted(out, key=lambda p: (not p.ok, -p.mbps))


def pick_node(stop_flag=None) -> tuple[Node | None, list[Probe]]:
    ps = probe_nodes(stop_flag=stop_flag)
    for p in ps:
        if p.ok:
            return p.node, ps
    return None, ps


# ------------------------------------------------------------------ 延迟
def latency(host: str, samples: int = 8,
            timeout: float = 1.2) -> tuple[float, float, str]:
    """TCP 三次握手耗时，返回 (平均 ms, 抖动 ms, 错误)。

    不用 ICMP：受管机器上 echo 常被安全软件吞掉，而"能不能建 TCP"才是有意义的。
    """
    rtts: list[float] = []
    err = ""
    for i in range(samples):
        try:
            t0 = time.perf_counter()
            s = socket.create_connection((host, 443), timeout=timeout)
            s.close()
            rtts.append((time.perf_counter() - t0) * 1000)
        except OSError as exc:
            err = f"{type(exc).__name__}: {exc}"
        if i < samples - 1:
            time.sleep(0.04)
    if not rtts:
        return 0.0, 0.0, err or "全部超时"
    # 第一条常含 DNS / 路由预热，样本足够时丢掉
    vals = rtts[1:] if len(rtts) > 3 else rtts
    avg = sum(vals) / len(vals)
    jit = (sum(abs(b - a) for a, b in zip(vals, vals[1:])) / (len(vals) - 1)
           if len(vals) > 1 else 0.0)
    return avg, jit, err


# ------------------------------------------------------------------ 下载
def _dl_thread(url: str, start: int, deadline: float, counter: _Counter,
               stop_flag, errs: list) -> None:
    pos = start
    while time.perf_counter() < deadline and not (stop_flag and stop_flag()):
        try:
            r = _open(url, timeout=8.0, rng=f"{pos}-")
            try:
                while True:
                    if time.perf_counter() >= deadline:
                        break
                    if stop_flag and stop_flag():
                        break
                    b = r.read(READ)
                    if not b:
                        break
                    counter.add(len(b))
            finally:
                try:
                    r.close()
                except Exception:       # noqa: BLE001
                    pass
        except Exception as exc:                # noqa: BLE001
            code = getattr(exc, "code", None)
            if code == 416:                     # 越过文件尾，回 0 重来
                pos = 0
                continue
            if not (stop_flag and stop_flag()):
                errs.append(f"HTTP {code}" if code
                            else f"{type(exc).__name__}: {exc}"[:120])
            return
        pos = 0                                  # 读到 EOF，从头循环


def _measure(jobs: list, target, seconds: float, on_progress, stop_flag,
             frac0: float, frac1: float, label: str):
    """跑一段固定时长的并发传输并采样。

    `jobs` 是每个线程要干的活（下载是 (url, 起始偏移)，上传是 None）。
    返回 (稳态 Mbps, 峰值 Mbps, 实际秒数, 错误, 是否取消, 曲线 [(相对秒, Mbps)], 字节数)。
    """
    counter = _Counter()
    errs: list[str] = []
    deadline = time.perf_counter() + seconds
    ts = [threading.Thread(target=target,
                           args=(job, i, deadline, counter, stop_flag, errs),
                           daemon=True)
          for i, job in enumerate(jobs)]
    t0 = time.perf_counter()
    for t in ts:
        t.start()

    marks: list[tuple[float, int]] = []
    series: list[tuple[float, float]] = []
    peak = 0.0
    stopped = False
    while True:
        if time.perf_counter() >= deadline:
            break
        if stop_flag and stop_flag():
            stopped = True
            break
        time.sleep(TICK)
        rel = time.perf_counter() - t0
        total = counter.get()
        marks.append((rel, total))
        # 瞬时速度按最近 1 秒算，不用单个 250ms 窗口：
        # 上传是「4 MB 一个请求」，单窗口会出现 0 / 400 Mbps 这种跳变，
        # 画成曲线全是尖刺，看不出真实水平。
        inst = 0.0
        if len(marks) >= 5:
            t_p, b_p = marks[-5]
            inst = (total - b_p) * 8 / max(0.2, rel - t_p) / 1e6
            peak = max(peak, inst)
            series.append((rel, inst))
        if on_progress:
            on_progress(frac0 + (frac1 - frac0) * min(1.0, rel / seconds),
                        f"{label}{total * 8 / max(0.1, rel) / 1e6:.0f} Mbps",
                        inst)

    for t in ts:
        t.join(timeout=8)

    dt = max(0.2, time.perf_counter() - t0)
    total = counter.get()
    base = next((b for tt, b in marks if tt >= WARMUP), 0)
    window = max(0.1, dt - WARMUP) if dt > WARMUP + 0.3 else dt
    if total <= base:                       # 窗口太短，退回全程口径
        steady = total * 8 / dt / 1e6
    else:
        steady = (total - base) * 8 / window / 1e6
    return max(0.0, steady), peak, dt, errs, stopped, series, total


def _download(urls: list[str], seconds: float, on_progress, stop_flag,
              frac0: float, frac1: float):
    step = 4 * 1024 * 1024
    jobs = [(u, k * step) for u in urls for k in range(DL_THREADS)]

    def target(job, _i, deadline, counter, stop_flag, errs):
        _dl_thread(job[0], job[1], deadline, counter, stop_flag, errs)

    label = (f"下载测速（{len(urls)} 节点 × {DL_THREADS} 线）"
             if len(urls) > 1 else f"下载测速（{DL_THREADS} 线）")
    return _measure(jobs, target, seconds, on_progress, stop_flag,
                    frac0, frac1, label)


def _upload(seconds: float, on_progress, stop_flag, frac0: float, frac1: float):
    body = b"\x5a" * UP_BODY          # 固定字节：压缩无所谓，不冒 CPU 去 os.urandom

    def target(_job, _i, deadline, counter, stop_flag, errs):
        while time.perf_counter() < deadline and not (stop_flag and stop_flag()):
            r = None
            try:
                r = _open(UPLOAD_URL, timeout=15.0, data=body)
                r.read()
                counter.add(len(body))
            except Exception as exc:            # noqa: BLE001
                if not (stop_flag and stop_flag()):
                    errs.append(f"{type(exc).__name__}: {exc}"[:120])
                return
            finally:
                if r is not None:
                    try:
                        r.close()
                    except Exception:           # noqa: BLE001
                        pass

    return _measure([None] * UP_THREADS, target, seconds, on_progress, stop_flag,
                    frac0, frac1, f"上传测速（{UP_THREADS} 线）")


# ------------------------------------------------------------------ 结论
@dataclass
class Report:
    node: str = ""
    node_where: str = ""
    nodes: list = field(default_factory=list)
    down_mbps: float = 0.0
    down_peak: float = 0.0
    up_mbps: float = 0.0
    up_peak: float = 0.0
    up_ok: bool = False
    up_note: str = ""
    latency_ms: float = 0.0
    jitter_ms: float = 0.0
    seconds: float = 0.0
    down_window: float = 0.0
    up_window: float = 0.0
    traffic_mb: float = 0.0
    link_mbps: float = 0.0
    proxy: str = ""
    samples: list = field(default_factory=list)   # [(t, 下载 Mbps, 上传 Mbps)]
    errors: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    elapsed: float = 0.0
    stopped: bool = False

    # ---------------- 派生
    @property
    def ratio(self) -> float:
        """实测下载 ÷ 网卡协商速率。0 表示拿不到协商速率。"""
        if self.link_mbps <= 0 or self.down_mbps <= 0:
            return 0.0
        return self.down_mbps / self.link_mbps

    @property
    def down_grade(self) -> tuple[str, str]:
        return down_grade(self.down_mbps)

    @property
    def up_grade(self) -> tuple[str, str]:
        return up_grade(self.up_mbps) if self.up_ok else ("未测", "faint")

    @property
    def latency_grade(self) -> tuple[str, str]:
        return latency_grade(self.latency_ms)

    @property
    def jitter_grade(self) -> tuple[str, str]:
        return jitter_grade(self.jitter_ms)

    @property
    def level(self) -> str:
        if self.errors or self.down_mbps <= 0:
            return "bad"
        roles = [self.down_grade[1], self.up_grade[1], self.latency_grade[1],
                 self.jitter_grade[1]]
        if "bad" in roles:
            return "bad"
        if "warn" in roles:
            return "warn"
        return "ok"

    @property
    def headline(self) -> str:
        if self.down_mbps <= 0:
            return "测速没跑起来"
        bits = [f"下载 {mbps_text(self.down_mbps)}"]
        if self.up_ok:
            bits.append(f"上传 {mbps_text(self.up_mbps)}")
        if self.latency_ms:
            bits.append(f"延迟 {self.latency_ms:.0f} ms")
        return " · ".join(bits)

    def hints(self, wifi: bool = False) -> list[str]:
        out: list[str] = []
        if self.down_mbps <= 0:
            if self.stopped:
                return ["测试被手动停止，还没拿到下载速度。"]
            return ["下载测速失败：先确认这台机器能上网、系统代理或防火墙没拦住 HTTPS。"]

        out.append(tier_text(self.down_mbps))
        r = self.ratio
        # 网卡比宽带快是常态：2.5G / 10G 网口 + 千兆宽带，达成率只有三成多，
        # 这时候拿比例去吓人就是误报，必须单独说明。
        if self.link_mbps >= 2500:
            out.append(f"网卡协商 {self.link_mbps:.0f} Mbps（{'2.5G' if self.link_mbps < 5000 else '万兆'}"
                       " 网口），实测跑不满是宽带套餐决定的，不是网卡瓶颈。")
        elif r >= 0.85:
            out.append(f"实测已接近网卡协商速率（{self.link_mbps:.0f} Mbps），带宽基本跑满。")
        elif r >= 0.40:
            out.append(f"实测约为网卡速率（{self.link_mbps:.0f} Mbps）的 {r * 100:.0f}%，"
                       "通常就是宽带套餐的上限。")
        elif r > 0 and wifi:
            out.append(f"无线只跑到协商速率的 {r * 100:.0f}%。Wi-Fi 实际吞吐普遍只有"
                       "协商值的三到六成，距离、隔墙、2.4G 信道拥挤都会压低；"
                       "插网线再测一次才有可比性。")
        elif r > 0:
            out.append(f"有线只跑到协商速率的 {r * 100:.0f}%，先排除这几项："
                       "网线是不是超五类以上、是不是插在百兆口、"
                       "后台有没有下载或网盘在抢带宽。")
        if self.down_mbps < 50 and self.link_mbps >= 900:
            out.append("千兆网卡只跑出不到 50 Mbps，重点看网线和路由器端口。")
        # 代理/VPN 只在结果本身可疑时才提 —— 跑得动的时候提它只是噪音
        if self.proxy and self.down_mbps < 200:
            out.append(f"本次测速经过代理 {self.proxy}，测的是「含代理」的整条链路；"
                       "要纯宽带的数字，先关掉代理 / VPN 再测一次。")
        if self.up_ok and self.down_mbps >= 100 and self.up_mbps < self.down_mbps / 10:
            out.append(f"上传只有下载的 1/{self.down_mbps / max(1.0, self.up_mbps):.0f}，"
                       "家庭宽带普遍非对等（如 500/50），属正常；"
                       "要大量回传文件就先确认套餐的上行档位。")
        if self.latency_ms > 80:
            out.append(f"延迟 {self.latency_ms:.0f} ms 偏高，游戏、远程桌面、视频会议会明显卡顿；"
                       "换个近一点的测速节点能分辨是链路远还是本地慢。")
        if self.jitter_ms > 30:
            out.append(f"抖动 {self.jitter_ms:.0f} ms 偏大，多半是 Wi-Fi 干扰或链路拥塞，"
                       "语音通话会断续。")
        if self.up_note and not self.up_ok:
            out.append(f"上传没测成：{self.up_note}（不影响下载结论）")
        if insecure_used():
            out.append("HTTPS 证书被中间设备拦下，已降级为不校验 —— "
                       "本机可能装了抓包软件或走了企业网关。")
        if self.stopped:
            out.append("测试被手动停止，上面的数字只反映已跑完的部分。")
        return out


def tier_text(v: float) -> str:
    """按实测值反推「这是几兆的宽带」，比单纯给个数字有用。"""
    if v >= 700:
        return (f"实测 {mbps_text(v)}，已经是千兆档 —— "
                "千兆口实测天花板约 940 Mbps，跑不到 1000 是正常损耗。")
    if v >= 400:
        return f"实测 {mbps_text(v)}，大约是 500M 档宽带的上限。"
    if v >= 150:
        return f"实测 {mbps_text(v)}，大约对应 200~300M 档宽带。"
    if v >= 70:
        return f"实测 {mbps_text(v)}，大约对应 100M 档宽带。"
    if v >= 25:
        return f"实测 {mbps_text(v)}，大约对应 50M 档宽带。"
    return f"实测 {mbps_text(v)}，连 50M 档都没跑到。"


def mbps_text(v: float) -> str:
    if v <= 0:
        return "-"
    if v >= 1000:
        return f"{v / 1000:.2f} Gbps"
    if v >= 100:
        return f"{v:.0f} Mbps"
    return f"{v:.1f} Mbps"


def mb_text(v: float) -> str:
    if v >= 1024:
        return f"{v / 1024:.2f} GB"
    return f"{v:.0f} MB"


def down_grade(v: float) -> tuple[str, str]:
    if v >= 500:
        return "极快", "ok"
    if v >= 200:
        return "很快", "ok"
    if v >= 100:
        return "良好", "ok"
    if v >= 50:
        return "一般", "warn"
    if v > 0:
        return "偏慢", "warn"
    return "失败", "bad"


def up_grade(v: float) -> tuple[str, str]:
    if v >= 50:
        return "很好", "ok"
    if v >= 20:
        return "正常", "ok"
    if v >= 10:
        return "一般", "warn"
    if v > 0:
        return "偏慢", "warn"
    return "失败", "bad"


def latency_grade(v: float) -> tuple[str, str]:
    if v <= 0:
        return "未测", "faint"
    if v <= 20:
        return "优秀", "ok"
    if v <= 50:
        return "良好", "ok"
    if v <= 100:
        return "一般", "warn"
    return "偏高", "warn"


def jitter_grade(v: float) -> tuple[str, str]:
    if v <= 0:
        return "未测", "faint"
    if v <= 5:
        return "稳定", "ok"
    if v <= 15:
        return "轻微", "ok"
    if v <= 30:
        return "明显", "warn"
    return "严重", "bad"


# ------------------------------------------------------------------ 主流程
def run(node_key: str = "auto", seconds: int = 5, on_progress=None,
        stop_flag=None, link_mbps: float = 0.0, with_upload: bool = True) -> Report:
    """跑一次完整测速。

    `on_progress(frac, msg, inst_mbps)` 在 GUI 线程外被调用；
    `inst_mbps` 是当前 250 ms 窗口的瞬时速度，界面拿它画实时曲线。
    """
    t0 = time.perf_counter()
    rep = Report(seconds=float(seconds), link_mbps=link_mbps,
                 proxy=proxy_note())

    def stop() -> bool:
        return bool(stop_flag and stop_flag())

    def emit(frac: float, msg: str, inst: float = 0.0) -> None:
        if on_progress:
            on_progress(max(0.0, min(1.0, frac)), msg, inst)

    # ---- 1. 选节点
    emit(0.02, "探测测速节点…")
    nodes: list[Node] = []
    if node_key and node_key != "auto":
        one = node_of(node_key)
        if one is not None:
            nodes = [one]
            rep.notes.append("指定节点 " + one.name)
    if not nodes:
        best, probes = pick_node(stop)
        good = [p for p in probes if p.ok]
        if not good:
            rep.errors.append("三个镜像节点都连不上，先确认这台机器能访问外网"
                              "（或换个网络再试）")
            rep.elapsed = time.perf_counter() - t0
            return rep
        nodes = [good[0].node]
        # 第二名只要别差太远就一起压：单个镜像节点自己的吞吐就在 600~900 之间跳，
        # 两个节点各 4 线实测能收到 874~891，逐秒波动也从 ±300 变 ±80
        for p in good[1:MAX_NODES]:
            if p.mbps >= max(1.0, good[0].mbps * 0.35):
                nodes.append(p.node)
        rep.notes.append("自动选中 " + " + ".join(n.name for n in nodes)
                         + "（预拉 0.8 秒："
                         + "、".join(f"{p.node.name} {p.mbps:.0f}" for p in probes)
                         + " Mbps）")
    rep.node = " + ".join(n.name for n in nodes)
    rep.nodes = [n.name for n in nodes]
    rep.node_where = nodes[0].where

    if stop():
        rep.stopped = True
        rep.elapsed = time.perf_counter() - t0
        return rep

    # ---- 2. 延迟 / 抖动
    emit(0.08, "测量网络延迟…")
    ms, jit, lerr = latency(nodes[0].host)
    if lerr:
        rep.notes.append(f"延迟采样有丢包：{lerr}")
    rep.latency_ms, rep.jitter_ms = ms, jit
    if stop():
        rep.stopped = True
        rep.elapsed = time.perf_counter() - t0
        return rep

    # ---- 3. 下载
    emit(0.14, "开始下载测速…")
    span_end = 0.62 if with_upload else 0.96
    down, dpeak, dwin, derrs, dstop, dseries, _dbytes = _download(
        [n.url for n in nodes], float(seconds), on_progress, stop, 0.14, span_end)
    rep.down_mbps, rep.down_peak, rep.down_window = down, dpeak, dwin
    if derrs:
        rep.errors.extend(["下载：" + e for e in derrs[:2]])
    if dstop:
        rep.stopped = True

    # ---- 4. 上传
    useries: list = []
    if with_upload and not dstop:
        emit(0.64, "开始上传测速…")
        up, upeak, uwin, uerrs, ustop, useries, ubytes = _upload(
            float(seconds), on_progress, stop, 0.62, 0.96)
        rep.up_mbps, rep.up_peak, rep.up_window = up, upeak, uwin
        rep.up_ok = up > 0
        if not rep.up_ok:
            rep.up_note = uerrs[0] if uerrs else "上传节点没有回应"
        if ustop:
            rep.stopped = True
        rep.traffic_mb = (down * dwin + up * uwin) / 8
    else:
        rep.up_note = "未启用" if not with_upload else "未执行"
        rep.traffic_mb = down * dwin / 8

    # ---- 5. 拼曲线：下载段从 0 开始，上传段接在后面
    samples = [(t, v, 0.0) for t, v in dseries]
    off = dwin + 0.1
    samples += [(off + t, 0.0, v) for t, v in useries]
    rep.samples = samples

    rep.elapsed = time.perf_counter() - t0
    emit(1.0, "完成" if not rep.stopped else "已停止")
    return rep


def peek(node: Node, limit: int = 64 * 1024) -> tuple[int, float, str]:
    """真下一小段，返回 (取回字节, 首包 ms, 错误)。

    只为自检用：验证 DNS → TLS → `Range` 这条链路在打包版里也是通的
    （少了 `_ssl.pyd` 或根证书，症状就是这里失败、而 TCP 建连正常），
    64 KB 又不至于为了自检烧流量。
    """
    r = None
    try:
        t0 = time.perf_counter()
        r = _open(node.url, timeout=5.0, rng="0-")
        ttfb = (time.perf_counter() - t0) * 1000
        data = r.read(limit)
        return len(data), ttfb, ""
    except Exception as exc:            # noqa: BLE001
        return 0, 0.0, f"{type(exc).__name__}: {exc}"
    finally:
        if r is not None:
            try:
                r.close()
            except Exception:           # noqa: BLE001
                pass


def quick_check() -> tuple[bool, str]:
    """自检用：验节点配置 + 延迟 + 一小段 HTTPS（不真跑测速，不烧流量）。"""
    if not NODES:
        return False, "节点清单为空"
    for n in NODES:
        if not n.url.startswith("https://") or not n.host:
            return False, f"节点 {n.key} 配置不完整"
    if not 0 < WARMUP < 3:
        return False, "慢启动窗口不合理"
    if DL_THREADS < 2 or UP_THREADS < 1:
        return False, "并发数不合理"

    ms, jit, lerr = latency(NODES[0].host, samples=2, timeout=1.5)
    got, ttfb, herr = peek(NODES[0])
    if got:
        return True, (f"{len(NODES)} 个节点 · 延迟 {ms:.0f} ms · "
                      f"首包 {ttfb:.0f} ms · 取回 {got // 1024} KB")
    if ms > 0:
        # TCP 建得上、HTTPS 下不来 —— 打包缺 ssl / 根证书，或中间设备拦了证书
        return False, f"HTTPS 取包失败（TCP 正常）：{herr[:70]}"
    return True, f"{len(NODES)} 个节点 · 当前离线（延迟与 HTTPS 均未通，{lerr[:40]}）"
