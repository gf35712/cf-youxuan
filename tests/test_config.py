# -*- coding: utf-8 -*-
"""CLI 配置辅助模块契约测试。"""
import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config  # noqa: E402


def test_validators_accept_boundaries():
    assert config.positive_int("1") == 1
    assert config.port("0") == 0
    assert config.port("65535") == 65535
    assert config.positive_float("0.1") == pytest.approx(0.1)
    assert config.nonnegative_float("0") == 0
    assert config.nonnegative_int("0") == 0


def test_validators_reject_invalid_values():
    with pytest.raises(argparse.ArgumentTypeError):
        config.positive_int("0")
    with pytest.raises(argparse.ArgumentTypeError):
        config.port("65536")
    with pytest.raises(argparse.ArgumentTypeError):
        config.positive_float("0")
    with pytest.raises(argparse.ArgumentTypeError):
        config.nonnegative_int("-1")


def test_load_config_file_is_safe(tmp_path):
    missing = tmp_path / "missing.json"
    assert config.load_config_file(missing) == {}
    malformed = tmp_path / "bad.json"
    malformed.write_text("{bad", encoding="utf-8")
    assert config.load_config_file(malformed) == {}
    valid = tmp_path / "valid.json"
    valid.write_text(json.dumps({"tcp_workers": 12}), encoding="utf-8")
    assert config.load_config_file(valid) == {"tcp_workers": 12}


def test_find_config_path_variants():
    assert config.find_config_path(["--config", "x.json"]) == "x.json"
    assert config.find_config_path(["--config=x.json"]) == "x.json"
    assert config.find_config_path(["--config"]) is None
    assert config.find_config_path([]) is None
