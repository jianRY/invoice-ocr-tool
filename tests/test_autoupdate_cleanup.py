# -*- coding: utf-8 -*-
"""autoupdate.py 的「更新后删除旧版本」行为测试（不联网、不开窗）。

跑法：python tests/test_autoupdate_cleanup.py

覆盖：
  ① 跨进程标记文件：写→读→读完即删；缺失/损坏时**安全回落到 True（=旧行为）**
  ② _purge 两种模式：cleanup_old=False 一个字节都不删且如实列出残留；True 照旧清理
  ③ 配置持久化：cleanup_old 与既有 auto_update 互不覆盖
  ④ 向后兼容：所有新增参数的默认值都等于旧行为
"""
import inspect
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import autoupdate as AU  # noqa: E402

PASS, FAIL = [], []


def check(label, got, want):
    if got == want:
        PASS.append(label)
        print("  ok   %s = %r" % (label, got))
    else:
        FAIL.append(label)
        print("  FAIL %s: 实际 %r 期望 %r" % (label, got, want))


def main():
    d = tempfile.mkdtemp(prefix="au_cleanup_")
    try:
        cur = os.path.join(d, "App_v1.0.exe")
        old1 = os.path.join(d, "App_v1.0_旧版_20260101010101.exe")
        old2 = os.path.join(d, "App_v1.0_更新中.exe")
        other = os.path.join(d, "other.exe")
        for p in (cur, old1, old2, other):
            with open(p, "wb") as f:
                f.write(b"x")

        print("\n[1] 跨进程标记文件：写 → 读 → 读完即删")
        check("初始无标记时按旧行为 True",
              AU.read_and_consume_cleanup_flag(d), True)
        AU.write_cleanup_flag(d, False)
        check("写完存在", os.path.exists(os.path.join(d, AU._CLEANUP_FLAG)), True)
        check("读到 False", AU.read_and_consume_cleanup_flag(d), False)
        check("读完即删", os.path.exists(os.path.join(d, AU._CLEANUP_FLAG)), False)
        check("删掉后再读回落到 True", AU.read_and_consume_cleanup_flag(d), True)

        AU.write_cleanup_flag(d, True)
        check("读到 True", AU.read_and_consume_cleanup_flag(d), True)

        with open(os.path.join(d, AU._CLEANUP_FLAG), "w", encoding="utf-8") as f:
            f.write("这不是合法内容")
        check("损坏内容回落到 True（不能因机制故障改成「保留」）",
              AU.read_and_consume_cleanup_flag(d), True)

        print("\n[2] _purge(cleanup_old=False)：一个字节都不删，且如实列出残留")
        logs = []
        removed = AU._purge(d, cur, log_fn=logs.append, max_wait=0.1,
                            cleanup_old=False)
        check("返回空列表", removed, [])
        check("旧版文件都还在", sorted(os.listdir(d)),
              sorted(["App_v1.0.exe", "App_v1.0_旧版_20260101010101.exe",
                      "App_v1.0_更新中.exe", "other.exe"]))
        joined = " ".join(logs)
        check("日志提到保留", "已保留旧版本文件" in joined, True)
        check("日志列全了残留（不是「无残留」）",
              ("App_v1.0_旧版_20260101010101.exe" in joined
               and "App_v1.0_更新中.exe" in joined), True)

        print("\n[3] _purge(cleanup_old=True)：照旧清理，且不碰无关/当前文件")
        logs2 = []
        removed = AU._purge(d, cur, log_fn=logs2.append, max_wait=0.1,
                            cleanup_old=True)
        check("清掉 2 个", sorted(removed),
              sorted(["App_v1.0_旧版_20260101010101.exe", "App_v1.0_更新中.exe"]))
        check("当前程序未被动", os.path.exists(cur), True)
        check("无关文件未被动", os.path.exists(other), True)

        print("\n[4] 配置持久化：cleanup_old 与既有 auto_update 互不覆盖")
        cfg = os.path.join(d, "config.json")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"auto_update": False}, f)
        check("缺配置时默认 True", AU._load_cleanup_old(os.path.join(d, "无.json")), True)
        AU._save_cleanup_old(cfg, False)
        with open(cfg, "r", encoding="utf-8") as f:
            data = json.load(f)
        check("cleanup_old 已落盘", data.get("cleanup_old"), False)
        check("auto_update 未被覆盖", data.get("auto_update"), False)
        check("读回 False", AU._load_cleanup_old(cfg), False)
        AU._save_cleanup_old(cfg, True)
        check("读回 True", AU._load_cleanup_old(cfg), True)

        print("\n[5] 向后兼容：新增参数默认值必须等于旧行为")
        check("run_update_check.cleanup_old 默认 None",
              inspect.signature(AU.run_update_check).parameters["cleanup_old"].default,
              None)
        check("cleanup_old_versions.cleanup_old 默认 None",
              inspect.signature(AU.cleanup_old_versions)
              .parameters["cleanup_old"].default, None)
        check("settle_after_update.cleanup_old 默认 None",
              inspect.signature(AU.settle_after_update)
              .parameters["cleanup_old"].default, None)
        check("windows_replace_and_restart.cleanup_old 默认 True",
              inspect.signature(AU.windows_replace_and_restart)
              .parameters["cleanup_old"].default, True)
        check("DownloadProgressDialog.cleanup_old 默认 True",
              inspect.signature(AU.DownloadProgressDialog.__init__)
              .parameters["cleanup_old"].default, True)
        check("UpdateDialog.cleanup_old_default 默认 True",
              inspect.signature(AU.UpdateDialog.__init__)
              .parameters["cleanup_old_default"].default, True)

        print("\n[6] 掉速换源看门狗（最新格式）已就位")
        check("SWITCH_SPEED 存在", hasattr(AU, "SWITCH_SPEED"), True)
        check("_SwitchedSource 存在", hasattr(AU, "_SwitchedSource"), True)
        check("_SwitchedSource 是异常类",
              issubclass(AU._SwitchedSource, Exception), True)
        check("_download_one 存在",
              hasattr(AU.DownloadProgressDialog, "_download_one"), True)
        check("MIN_USEFUL_SPEED = 100KB", AU.MIN_USEFUL_SPEED, 100 * 1024)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print("\n" + "=" * 56)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
