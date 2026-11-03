# -*- coding: utf-8 -*-
"""网络诊断核心探针：逐项验证 ctypes 结构是否读对。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import netdiag  # noqa: E402

LOG = open("probe_net.log", "w", encoding="utf-8", buffering=1)


def w(s):
    LOG.write(str(s) + "\n")


def main():
    ads = netdiag.adapters()
    w(f"adapters = {len(ads)}")
    for a in ads:
        w(f"  {a.desc!r:44} ip={a.ip}/{a.mask:<16} gw={a.gateway:<16} "
          f"dns={a.dns:<24} dhcp={a.dhcp} speed={a.speed_text:10} "
          f"medium={a.medium} up={a.up}")

    pri = netdiag.primary()
    w(f"primary = {pri.title if pri else None} gw={pri.gateway if pri else '-'}")
    w(f"ping(127.0.0.1) = {netdiag.ping('127.0.0.1')}")
    if pri and pri.gateway:
        w(f"ping(gateway) = {netdiag.ping(pri.gateway)}")
    w(f"ping(223.5.5.5) = {netdiag.ping('223.5.5.5')}")
    w(f"ping(no-such-host) = {netdiag.ping('no-such-host.invalid')}")
    w(f"resolve(www.baidu.com) = {netdiag.resolve('www.baidu.com')}")
    w(f"tcp(127.0.0.1,445) = {netdiag.tcp_check('127.0.0.1', 445)}")
    w(f"tcp(127.0.0.1,1) = {netdiag.tcp_check('127.0.0.1', 1, 0.4)}")

    ls = netdiag.listening()
    w(f"listeners = {len(ls)}")
    pmap = netdiag._proc_map()
    w(f"proc_map = {len(pmap)} entries, sample={list(pmap.items())[:4]}")
    for it in ls[:8]:
        w(f"  {it.proto}:{it.addr}:{it.port} pid={it.pid} "
          f"proc={pmap.get(it.pid, '')}")

    w(f"proxy = {netdiag.proxy()}")
    n, ent, level, note = netdiag.hosts_entries()
    w(f"hosts lines={n} entries={len(ent)} level={level!r} note={note!r}")
    for ip, name in ent[:6]:
        w(f"  {ip} -> {name}")

    rep = netdiag.diagnose()
    w(f"diagnose elapsed={rep.elapsed:.2f}s checks={len(rep.checks)} "
      f"listeners={len(rep.listeners)} problems={len(rep.problems)}")
    for c in rep.checks:
        flag = {True: "OK ", False: "BAD", None: "... "}[c.ok]
        w(f"  [{flag}] {c.name:24} | {c.value:46} | {c.hint}")
    w("DONE")
    LOG.close()


if __name__ == "__main__":
    main()
