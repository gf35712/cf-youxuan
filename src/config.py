# -*- coding: utf-8 -*-
"""CLI 配置辅助：参数类型校验与 JSON 默认值加载。

该模块不依赖业务执行流程，便于 CLI、测试和后续配置界面复用。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def positive_int(value: str) -> int:
    v = int(value)
    if v <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value}")
    return v


def port(value: str) -> int:
    try:
        v = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid port: {value!r}") from None
    if not 0 <= v <= 65535:
        raise argparse.ArgumentTypeError(f"port out of range: {value}")
    return v


def positive_float(value: str) -> float:
    v = float(value)
    if v <= 0:
        raise argparse.ArgumentTypeError(f"must be > 0, got {value}")
    return v


def nonnegative_float(value: str) -> float:
    v = float(value)
    if v < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {value}")
    return v


def nonnegative_int(value: str) -> int:
    try:
        v = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid non-negative integer: {value!r}") from None
    if v < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {value}")
    return v


def load_config_file(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def apply_config_defaults(parser: argparse.ArgumentParser, config_path: str) -> None:
    """将 JSON 中存在且能通过 argparse 类型校验的字段设为默认值。"""
    cfg = load_config_file(Path(config_path))
    if not cfg:
        return
    defaults = {}
    for action in parser._actions:
        dest = action.dest
        if dest in ("help", "config") or dest not in cfg:
            continue
        value = cfg[dest]
        if isinstance(action, argparse._StoreTrueAction):
            value = bool(value)
        elif action.type is not None:
            try:
                value = action.type(value)
            except (ValueError, TypeError, argparse.ArgumentTypeError):
                continue
        defaults[dest] = value
    if defaults:
        parser.set_defaults(**defaults)


def find_config_path(argv: list[str]) -> str | None:
    for i, arg in enumerate(argv):
        if arg == "--config":
            return argv[i + 1] if i + 1 < len(argv) else None
        if arg.startswith("--config="):
            return arg.split("=", 1)[1]
    return None


# update.py 的旧私有名称仍可被兼容调用。
_positive_int = positive_int
_port = port
_positive_float = positive_float
_nonnegative_float = nonnegative_float
_nonnegative_int = nonnegative_int
_apply_config_defaults = apply_config_defaults
_find_config_path = find_config_path
