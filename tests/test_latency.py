# -*- coding: utf-8 -*-
"""延迟模块契约测试。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import latency  # noqa: E402


def test_positive_worker_count_boundaries():
    assert latency.positive_worker_count(-1, 0) == 1
    assert latency.positive_worker_count(3, 2) == 2
    assert latency.positive_worker_count(3, 10) == 3


@pytest.mark.asyncio
async def test_tcping_samples_returns_minimum(monkeypatch):
    values = iter([30.0, None, 12.0])

    async def fake_once(node, timeout):
        return next(values)

    monkeypatch.setattr(latency, "_tcping_once", fake_once)
    result = await latency.tcping(latency.Node("192.0.2.1", 443, ""), timeout=1, samples=3)
    assert result == 12.0


@pytest.mark.asyncio
async def test_httping_samples_returns_minimum(monkeypatch):
    values = iter([40.0, 18.0, None])

    async def fake_once(node, timeout, domain):
        return next(values)

    monkeypatch.setattr(latency, "_httping_once", fake_once)
    result = await latency.httping(latency.Node("192.0.2.1", 443, ""), timeout=1, samples=3)
    assert result == 18.0

@pytest.mark.asyncio
async def test_tcping_stats_reports_packet_loss(monkeypatch):
    values = iter([12.0, None, 18.0, None])

    async def fake_once(node, timeout):
        return next(values)

    monkeypatch.setattr(latency, "_tcping_once", fake_once)
    stats = await latency.tcping_stats(latency.Node("192.0.2.1", 443, ""), timeout=1, samples=4)
    assert stats.latency_ms == 12.0
    assert stats.sent == 4 and stats.received == 2
    assert stats.packet_loss == 50.0
