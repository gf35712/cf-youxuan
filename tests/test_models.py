# -*- coding: utf-8 -*-
"""共享模型的契约测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import models  # noqa: E402
import update  # noqa: E402


def test_node_raw_ipv4_and_ipv6():
    assert models.Node("1.1.1.1", 443, "US").raw == "1.1.1.1:443#US"
    assert models.Node("2001:db8::1", 443, "").raw == "[2001:db8::1]:443#"


def test_update_reexports_shared_models():
    assert update.Node is models.Node
    assert update.TcpResult is models.TcpResult
    assert update.SpeedResult is models.SpeedResult


def test_speed_result_keeps_model_fields():
    node = models.Node("192.0.2.1", 443, "TEST")
    result = models.SpeedResult(node, 12.5, 88.0, True)
    assert result.node is node
    assert result.latency_ms == 12.5
    assert result.speed_mbps == 88.0
    assert result.is_fast is True
