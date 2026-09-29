# -*- coding: utf-8 -*-
"""开发/构建脚本共用的**本机配置**读取（只读，仓库里不含任何开发机路径）。

取值链（优先级从高到低）：
    ① 环境变量                        —— 临时覆盖
    ② .pybuild_cache/local_config.json —— 本机长期配置，已被 .gitignore 排除
    ③ 传入的 default                  —— 通常是项目内相对路径

配置文件位置可用环境变量 `OCRTOOL_CONFIG` 指向别处。
**发布产物（exe）不依赖本模块**，它只服务于 release.py 这类在开发者机器上跑的脚本。
"""
import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
_CFG_PATH = os.environ.get("OCRTOOL_CONFIG") or os.path.join(
    ROOT, ".pybuild_cache", "local_config.json")


def _load():
    try:
        with open(_CFG_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


_CFG = _load()


def get(env_key, json_key, default=""):
    """按 环境变量 -> 本机配置 -> default 取一项配置，返回字符串。"""
    v = (os.environ.get(env_key) or "").strip()
    if v:
        return v
    v = _CFG.get(json_key)
    if isinstance(v, str) and v.strip():
        return v.strip()
    return default
