# -*- coding: utf-8 -*-
"""主流程参数透传契约测试。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import update  # noqa: E402


@pytest.mark.asyncio
async def test_run_forwards_proxy_to_speed_tests(tmp_path, monkeypatch):
    input_file = tmp_path / "ips.txt"
    input_file.write_text("8.134.218.35:443#DE\n", encoding="utf-8")
    captured = {}

    async def fake_tcping(node, timeout, samples=1):
        return 10.0

    async def fake_speed(*args):
        captured["proxy"] = args[9]
        return []

    monkeypatch.setattr(update, "tcping", fake_tcping)
    monkeypatch.setattr(update, "run_speed_tests", fake_speed)
    monkeypatch.setattr(update, "benchmark_network", lambda config: None)

    cfg = update.AppConfig(
        input_file=input_file,
        full_output_file=tmp_path / "full.txt",
        best_output_file=tmp_path / "best.txt",
        input_explicit=True,
        benchmark_enabled=False,
        proxy="http://127.0.0.1:7890",
        speed_url="https://speed.cloudflare.com/__down",
        top_per_region=1,
    )
    assert await update.run(cfg) == 0
    assert captured["proxy"] == "http://127.0.0.1:7890"


@pytest.mark.asyncio
async def test_run_separates_input_proxy_from_speed_proxy(tmp_path, monkeypatch):
    input_file = tmp_path / "ips.txt"
    input_file.write_text("8.134.218.35:443#DE\n", encoding="utf-8")
    (tmp_path / "_ips_url.txt").write_text("https://cm.example/all.txt", encoding="utf-8")
    captured = {}

    def fake_refresh(url, path, timeout, **kwargs):
        captured["input_proxy"] = kwargs.get("proxy")
        path.write_text("8.134.218.35:443#DE\n", encoding="utf-8")
        return True

    async def fake_tcping(node, timeout, samples=1):
        return 10.0

    async def fake_speed(*args):
        captured["speed_proxy"] = args[9]
        return []

    monkeypatch.setattr(update, "refresh_input_file", fake_refresh)
    monkeypatch.setattr(update, "run_speed_tests", fake_speed)
    monkeypatch.setattr(update, "benchmark_network", lambda config: None)
    monkeypatch.chdir(tmp_path)
    cfg = update.AppConfig(
        input_file=input_file, full_output_file=tmp_path / "full.txt",
        best_output_file=tmp_path / "best.txt", benchmark_enabled=False,
        input_proxy="http://127.0.0.1:7890", speed_proxy="",
        input_explicit=False,
    )
    monkeypatch.setattr(update, "tcping", fake_tcping)
    assert await update.run(cfg) == 0
    assert captured.get("input_proxy") == "http://127.0.0.1:7890"
    assert captured.get("speed_proxy") == ""

@pytest.mark.asyncio
async def test_recheck_latency_records_loss_for_top_k(monkeypatch):
    async def fake_stats(node, timeout, samples):
        return update.LatencyStats(12.0, 3, 2) if node.ip == "192.0.2.1" else update.LatencyStats(30.0, 3, 3)

    monkeypatch.setattr(update, "tcping_stats", fake_stats)
    results = [
        update.TcpResult(update.Node("192.0.2.1", 443, ""), 10.0),
        update.TcpResult(update.Node("192.0.2.2", 443, ""), 20.0),
    ]
    out = await update.recheck_latency_results(results, timeout=1, top_k=1, samples=3)
    assert out[0].latency_ms == 12.0
    assert out[0].packet_loss == pytest.approx(33.33, abs=0.01)
    assert out[1].packet_loss == 0.0


@pytest.mark.asyncio
async def test_recheck_all_failures_keep_initial_success(monkeypatch, capsys):
    async def failed_stats(node, timeout, samples):
        return update.LatencyStats(None, samples, 0)

    monkeypatch.setattr(update, "tcping_stats", failed_stats)
    original = update.TcpResult(update.Node("192.0.2.10", 443, "HK"), 18.0)
    out = await update.recheck_latency_results([original], timeout=0.5, top_k=1, samples=3)
    assert out[0].latency_ms == 18.0
    assert out[0].packet_loss == 0.0
    assert "failed rechecks kept: 1" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_recover_failed_tcp_tests_only_retries_missing_nodes(monkeypatch, capsys):
    nodes = [
        update.Node("192.0.2.20", 443, "HK"),
        update.Node("192.0.2.21", 443, "JP"),
    ]
    successful = [update.TcpResult(nodes[0], 12.0)]
    captured = {}

    async def fake_run(nodes_to_test, timeout, workers, verbose, json_progress=False,
                       samples=1, mode="tcping"):
        captured.update({"nodes": list(nodes_to_test), "timeout": timeout, "workers": workers})
        return [update.TcpResult(nodes_to_test[0], 25.0)]

    monkeypatch.setattr(update, "run_tcp_tests", fake_run)
    out = await update.recover_failed_tcp_tests(
        nodes, successful, timeout=0.5, workers=150, verbose=False)
    assert [item.node.ip for item in out] == ["192.0.2.21"]
    assert captured["timeout"] == 1.0
    assert captured["workers"] == 64
    assert "TCP recovery pass complete: recovered 1/1 nodes" in capsys.readouterr().out
