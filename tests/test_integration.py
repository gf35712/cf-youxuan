# -*- coding: utf-8 -*-
"""集成测试 — 本地模拟 TCP/HTTP 服务器测试 update.py 核心异步函数。"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import update  # noqa: E402


@pytest.fixture
async def tcp_server():
    """启动本地 TCP echo 服务器，返回 (host, port)。"""

    async def _echo(reader, writer):
        try:
            await asyncio.wait_for(reader.read(4096), timeout=2.0)
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    server = await asyncio.start_server(_echo, host="127.0.0.1", port=0)
    addr = server.sockets[0].getsockname()
    async with server:
        yield addr


@pytest.fixture
async def raw_http_server():
    """启动本地 HTTP 服务器（返回固定字节）。"""

    async def handler(reader, writer):
        try:
            await asyncio.wait_for(reader.read(4096), timeout=2.0)
            body = b"x" * 65536
            resp = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Length: 65536\r\n"
                b"Connection: close\r\n"
                b"\r\n"
            ) + body
            writer.write(resp)
            await writer.drain()
        except (asyncio.TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    server = await asyncio.start_server(handler, host="127.0.0.1", port=0)
    addr = server.sockets[0].getsockname()
    async with server:
        yield addr


class TestTcping:
    @pytest.mark.asyncio
    async def test_tcping_success(self, tcp_server):
        host, port = tcp_server
        node = update.Node(host, port, "TEST")
        latency = await update.tcping(node, timeout=2.0)
        assert latency is not None
        assert isinstance(latency, float)
        assert latency > 0

    @pytest.mark.asyncio
    async def test_tcping_samples(self, tcp_server):
        host, port = tcp_server
        node = update.Node(host, port, "TEST")
        latency = await update.tcping(node, timeout=2.0, samples=3)
        assert latency is not None
        assert latency > 0

    @pytest.mark.asyncio
    async def test_tcping_unreachable(self):
        node = update.Node("127.0.0.1", 1, "TEST")  # 本机端口 1 几乎必然拒绝连接
        latency = await update.tcping(node, timeout=0.5)
        assert latency is None

    @pytest.mark.asyncio
    async def test_tcping_close_error_swallowed(self, monkeypatch):
        """writer 关闭抛 OSError 被吞掉，仍返回延迟。"""
        async def fake_open(host, port):
            class BadWriter:
                def close(self):
                    pass

                async def wait_closed(self):
                    raise OSError("reset")

            class FakeReader:
                async def read(self, n=-1):
                    return b""

            return FakeReader(), BadWriter()

        monkeypatch.setattr(update.asyncio, "open_connection", fake_open)
        node = update.Node("127.0.0.1", 1, "A")
        latency = await update.tcping(node, timeout=0.5)
        assert latency is not None and latency >= 0


class TestRunTcpTests:
    @pytest.mark.asyncio
    async def test_real_tcp(self, tcp_server):
        host, port = tcp_server
        nodes = [update.Node(host, port, "A")]
        results = await update.run_tcp_tests(nodes, timeout=1.0, workers=2, verbose=False)
        assert len(results) == 1
        assert results[0].latency_ms > 0

    @pytest.mark.asyncio
    async def test_json_progress_emits_json(self, tcp_server, capsys):
        host, port = tcp_server
        nodes = [update.Node(host, port, "A")]
        await update.run_tcp_tests(nodes, timeout=1.0, workers=1, verbose=False,
                                   json_progress=True)
        out = capsys.readouterr().out
        assert '"progress"' in out


class TestVerify:
    @pytest.mark.asyncio
    async def test_verify_keeps_on_failure(self, monkeypatch):
        """验证重测失败（0.0，网络抖动）-> 保留原结果，不误杀。"""
        async def fake_speed(node, timeout, speed_bytes, proxy="", speed_url="", speed_port=0):
            return 80.0 if node.ip == "1.1.1.1" else 0.0

        monkeypatch.setattr(update, "measure_speed_native", fake_speed)
        monkeypatch.setattr(update, "measure_speed_with_curl",
                            lambda *a, **k: 0.0)
        cfg = update.AppConfig(speed_engine="native", verify_top=10, min_speed_mbps=8)
        results = [
            update.SpeedResult(update.Node("1.1.1.1", 443, "HK"), 10.0, 50.0, True),
            update.SpeedResult(update.Node("2.2.2.2", 443, "HK"), 20.0, 60.0, True),
        ]
        out = await update.verify_best_results(results, cfg)
        # 2.2.2.2 验证失败（0.0）但保留原结果
        assert len(out) == 2
        assert {r.node.ip for r in out} == {"1.1.1.1", "2.2.2.2"}

    @pytest.mark.asyncio
    async def test_verify_drops_slow(self, monkeypatch):
        """验证重测成功但明显变慢（< 50%）才丢弃。"""
        def fake_curl(node, timeout, process_buffer, speed_bytes, proxy="", speed_url="", speed_port=0):
            return 10.0  # 原 50 -> 10，低于 50% 

        monkeypatch.setattr(update, "measure_speed_with_curl", fake_curl)
        cfg = update.AppConfig(speed_engine="curl", verify_top=10, min_speed_mbps=8)
        results = [update.SpeedResult(update.Node("1.1.1.1", 443, "HK"), 10.0, 50.0, True)]
        out = await update.verify_best_results(results, cfg)
        assert out == []  # 10 < 50*0.5=25，丢弃

    @pytest.mark.asyncio
    async def test_verify_retries_once_on_jitter(self, monkeypatch):
        """单次重测 0.0（网络抖动）-> 重试一次成功 -> 保留。"""
        calls = {"n": 0}

        def fake_curl(node, timeout, process_buffer, speed_bytes, proxy="", speed_url="", speed_port=0):
            calls["n"] += 1
            return 0.0 if calls["n"] == 1 else 60.0

        monkeypatch.setattr(update, "measure_speed_with_curl", fake_curl)
        cfg = update.AppConfig(speed_engine="curl", verify_top=10, min_speed_mbps=8)
        results = [update.SpeedResult(update.Node("1.1.1.1", 443, "HK"), 10.0, 50.0, True)]
        out = await update.verify_best_results(results, cfg)
        assert len(out) == 1
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_verify_disabled(self, monkeypatch):
        cfg = update.AppConfig(verify_top=0)
        results = [update.SpeedResult(update.Node("1.1.1.1", 443, "HK"), 10.0, 50.0, True)]
        assert await update.verify_best_results(results, cfg) == results


class TestNativeDownload:
    @pytest.mark.asyncio
    async def test_native_download_measures_speed(self, raw_http_server):
        host, port = raw_http_server
        # 原生引擎直连本地服务器（无 TLS），模拟 reader/writer
        reader, writer = await asyncio.open_connection(host, port)
        # 直接发一个简单 HTTP 请求（模拟 _native_download 前已建立连接）
        speed = await update._native_download(reader, writer, 5.0, 65536, "http://localhost:1/x", port)
        assert speed > 0