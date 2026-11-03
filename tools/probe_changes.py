# -*- coding: utf-8 -*-
"""变更记录采集探针：只读，不执行任何还原。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import changes  # noqa: E402

LOG = open("probe_changes.log", "w", encoding="utf-8", buffering=1)


def w(s):
    LOG.write(str(s) + "\n")


def main():
    rows, s = changes.collect()
    w(f"total={s.total} active={s.active_total} "
      f"(startup={s.active_startup} service={s.active_service} tweak={s.active_tweak}) "
      f"restored={s.restored} pending={s.pending}")
    w("-" * 100)
    for c in rows[:25]:
        w(f"{c.when or '-':20} | {c.module:4} | {c.action:8} | {c.target[:26]:28} | "
          f"{c.detail[:52]:54} | {c.state_text}")
    w("-" * 100)
    only_active, s2 = changes.collect(include_restored=False)
    w(f"only_active rows={len(only_active)} active={s2.active_total}")
    w("DONE")
    LOG.close()


if __name__ == "__main__":
    main()
