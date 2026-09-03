# -*- coding: utf-8 -*-
"""测速模块契约测试。"""
import sys
import subprocess
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import speed_test  # noqa: E402
import update  # noqa: E402


def test_parse_curl_speed_edge_cases():
    assert speed_test.parse_curl_speed("2097152 1.0 0.5") == pytest.approx(33.55, abs=0.01)
    assert speed_test.parse_curl_speed("1048576 1.0 1.5") == pytest.approx(8.39, abs=0.01)
    assert speed_test.parse_curl_speed("bad") == 0.0


def test_parse_curl_speed_rejects_non_success_status():
    assert speed_test.parse_curl_speed("1048576 1.0 0.5 200") > 0
    assert speed_test.parse_curl_speed("1048576 1.0 0.5 403") == 0.0
    assert speed_test.parse_curl_speed("1048576 1.0 0.5 429") == 0.0


def test_curl_speed_command_requests_http_status(monkeypatch):
    class Completed:
        stdout = "1048576 1.0 0.5 200"

    monkeypatch.setattr(update, "get_curl_command", lambda: "curl.exe")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return Completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = speed_test.measure_speed_with_curl(
        speed_test.Node("192.0.2.1", 443, ""), 1.0, 1.0, 1024,
        speed_url="https://speed.example.com/__down")
    assert result > 0
    assert any("%{http_code}" in part for part in captured["cmd"])


@pytest.mark.asyncio
async def test_native_download_closes_writer_on_failure():
    class BadWriter:
        def __init__(self):
            self.closed = False
            self.waited = False

        def write(self, data):
            raise OSError("write failed")

        async def drain(self):
            pass

        def close(self):
            self.closed = True

        async def wait_closed(self):
            self.waited = True

    class Reader:
        async def read(self, _size):
            return b""

    writer = BadWriter()
    result = await speed_test._native_download(Reader(), writer, 1.0, 1024)
    assert result == 0.0
    assert writer.closed is True and writer.waited is True


@pytest.mark.asyncio
async def test_native_speed_rejects_non_http_proxy():
    node = speed_test.Node("192.0.2.1", 443, "")
    assert await speed_test.measure_speed_native(node, 1.0, 1024, proxy="socks5://127.0.0.1:1080") == 0.0


def test_update_reexports_speed_implementation():
    assert update.parse_curl_speed is speed_test.parse_curl_speed
    assert update._native_download is speed_test._native_download
    assert update.measure_speed_native is speed_test.measure_speed_native

@pytest.mark.asyncio
async def test_run_speed_tests_stops_after_fast_target(monkeypatch, capsys):
    async def fake_speed(node, timeout, speed_bytes, proxy="", speed_url="", speed_port=0):
        return 20.0

    monkeypatch.setattr(update, "measure_speed_native", fake_speed)
    candidates = [
        update.TcpResult(update.Node(f"192.0.2.{i}", 443, ""), 10.0 + i)
        for i in range(1, 6)
    ]
    results = await update.run_speed_tests(
        candidates, timeout=1.0, process_buffer=1.0, workers=1, min_speed=8.0,
        verbose=False, engine="native", speed_bytes=1024, stop_after_fast=2)
    assert len(results) == 2
    assert "Early stop" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_run_speed_tests_prioritizes_best_candidates_before_early_stop(monkeypatch):
    async def fake_speed(node, timeout, speed_bytes, proxy="", speed_url="", speed_port=0):
        return 20.0

    monkeypatch.setattr(update, "measure_speed_native", fake_speed)
    candidates = [
        update.TcpResult(update.Node("192.0.2.30", 443, ""), 30.0),
        update.TcpResult(update.Node("192.0.2.10", 443, ""), 10.0),
        update.TcpResult(update.Node("192.0.2.20", 443, ""), 20.0),
    ]
    results = await update.run_speed_tests(
        candidates, timeout=1.0, process_buffer=1.0, workers=1, min_speed=8.0,
        verbose=False, engine="native", speed_bytes=1024, stop_after_fast=1)
    assert len(results) == 1
    assert results[0].latency_ms == 10.0


@pytest.mark.asyncio
async def test_run_speed_tests_uses_packet_loss_as_tie_breaker(monkeypatch):
    async def fake_speed(node, timeout, speed_bytes, proxy="", speed_url="", speed_port=0):
        return 20.0

    monkeypatch.setattr(update, "measure_speed_native", fake_speed)
    candidates = [
        update.TcpResult(update.Node("192.0.2.30", 443, ""), 10.0, packet_loss=20.0),
        update.TcpResult(update.Node("192.0.2.10", 443, ""), 10.0, packet_loss=0.0),
    ]
    results = await update.run_speed_tests(
        candidates, timeout=1.0, process_buffer=1.0, workers=1, min_speed=8.0,
        verbose=False, engine="native", speed_bytes=1024, stop_after_fast=1)
    assert len(results) == 1
    assert results[0].node.ip == "192.0.2.10"


@pytest.mark.asyncio
async def test_run_speed_tests_retains_worker_failure_as_zero_result(monkeypatch, capsys):
    async def failed_speed(*args, **kwargs):
        raise RuntimeError("synthetic speed failure")

    monkeypatch.setattr(update, "measure_speed_native", failed_speed)
    candidate = update.TcpResult(update.Node("192.0.2.99", 443, "HK"), 12.0)
    results = await update.run_speed_tests(
        [candidate], timeout=1.0, process_buffer=1.0, workers=1, min_speed=8.0,
        verbose=False, engine="native", speed_bytes=1024)
    assert len(results) == 1
    assert results[0].speed_mbps == 0.0
    assert results[0].is_fast is False
    assert "Speed test errors: 1" in capsys.readouterr().out
