# -*- coding: utf-8 -*-
"""端到端测试：mock 网络依赖覆盖 run() 主流程。"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import update  # noqa: E402


def _mk_node(ip, port=443, region="A"):
    return update.Node(ip, port, region)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """隔离工作目录 + 已下载的 ips.txt。"""
    ips = tmp_path / "ips.txt"
    ips.write_text("1.1.1.1:443#A\n2.2.2.2:443#B\n3.3.3.3:443#C\n", encoding="utf-8")

    def fake_refresh(url, path, timeout, **kwargs):
        return True

    monkeypatch.setattr(update, "refresh_input_file", fake_refresh)
    # run() 默认会探测测速源；e2e 必须完全离线，避免真实网络和 ResourceWarning 影响结果。
    monkeypatch.setattr(update, "_auto_detect_speed_url", lambda *args, **kwargs: "https://test.local/__down")
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_full_run(self, sandbox, monkeypatch, capsys):
        async def fake_tcping(node, timeout, samples=1):
            return {"1.1.1.1": 10.0, "2.2.2.2": 20.0}.get(node.ip)

        monkeypatch.setattr(update, "tcping", fake_tcping)

        async def fake_speed(candidates, timeout, process_buffer, workers, min_speed,
                             verbose, json_progress=False,
                             speed_bytes=update.DEFAULT_SPEED_BYTES,
                             engine="curl", proxy="", speed_url="", speed_port=0,
                             speed_samples=1):
            return [update.SpeedResult(c.node, c.latency_ms, 50.0, True)
                    for c in candidates]

        monkeypatch.setattr(update, "run_speed_tests", fake_speed)
        async def _no_verify(results, config):
            return results
        monkeypatch.setattr(update, "verify_best_results", _no_verify)
        async def _no_benchmark(config):
            return None
        monkeypatch.setattr(update, "benchmark_network", _no_benchmark)

        cfg = update.AppConfig(
            input_file=Path("ips.txt"), full_output_file=Path("full_ips.txt"),
            best_output_file=Path("best_ips.txt"),
            tcp_timeout=0.5, tcp_workers=2, verbose=False, json_progress=True,
            input_explicit=True,
        )
        rc = await update.run(cfg)
        assert rc == 0
        full = Path("full_ips.txt").read_text(encoding="utf-8")
        best = Path("best_ips.txt").read_text(encoding="utf-8")
        assert "1.1.1.1" in full and "2.2.2.2" in full
        assert "3.3.3.3" not in full  # 不可达
        assert best.strip()
        out = capsys.readouterr().out
        assert "Stage 1/2" in out and "Stage 1/2 complete" in out
        assert "Stage 2/2 starting" in out and "Done" in out

    @pytest.mark.asyncio
    async def test_explicit_input_never_downloads(self, sandbox, monkeypatch):
        """显式 -i 缺失文件时直接报错，不联网下载（回归：曾卡在下载默认源）。"""
        downloaded = []

        def fake_refresh(url, path, timeout, **kwargs):
            downloaded.append(url)
            return False

        monkeypatch.setattr(update, "refresh_input_file", fake_refresh)
        # 删掉 sandbox 创建的 ips.txt，模拟文件缺失
        Path("ips.txt").unlink()

        cfg = update.AppConfig(
            input_file=Path("ips.txt"), full_output_file=Path("full_ips.txt"),
            best_output_file=Path("best_ips.txt"),
            input_explicit=True,  # 显式 -i
        )
        rc = await update.run(cfg)
        assert rc == 1
        assert downloaded == []  # 绝不应触发下载

    @pytest.mark.asyncio
    async def test_cached_hit(self, sandbox, monkeypatch):
        nodes = [_mk_node("1.1.1.1")]
        monkeypatch.setattr(update, "load_nodes", lambda p: nodes)
        update.save_tcp_cache(Path("tcp_cache.json"), [update.TcpResult(nodes[0], 3.5)])

        async def fake_speed(candidates, timeout, process_buffer, workers, min_speed,
                             verbose, json_progress=False,
                             speed_bytes=update.DEFAULT_SPEED_BYTES,
                             engine="curl", proxy="", speed_url="", speed_port=0,
                             speed_samples=1):
            return [update.SpeedResult(c.node, c.latency_ms, 50.0, True)
                    for c in candidates]

        monkeypatch.setattr(update, "run_speed_tests", fake_speed)
        async def _no_verify(results, config):
            return results
        monkeypatch.setattr(update, "verify_best_results", _no_verify)
        async def _no_benchmark(config):
            return None
        monkeypatch.setattr(update, "benchmark_network", _no_benchmark)

        cfg = update.AppConfig(
            input_file=Path("ips.txt"), full_output_file=Path("full_ips.txt"),
            best_output_file=Path("best_ips.txt"),
            tcp_timeout=0.5, tcp_workers=2, verbose=False, json_progress=True,
            input_explicit=True,
        )
        rc = await update.run(cfg)
        assert rc == 0
        assert "1.1.1.1" in Path("best_ips.txt").read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_output_same_file(self, sandbox):
        cfg = update.AppConfig(
            input_file=Path("ips.txt"),
            full_output_file=Path("same.txt"),
            best_output_file=Path("same.txt"),
        )
        rc = await update.run(cfg)
        assert rc == 1

    @pytest.mark.asyncio
    async def test_empty_ips_url_falls_back(self, sandbox, monkeypatch):
        used = {}

        def fake_refresh(url, path, timeout, **kwargs):
            used["url"] = url
            Path(path).write_text("1.1.1.1:443#A\n", encoding="utf-8")
            return True

        monkeypatch.setattr(update, "refresh_input_file", fake_refresh)
        Path("_ips_url.txt").write_text("   ", encoding="utf-8")

        async def fake_tcping(node, timeout, samples=1):
            return 10.0

        monkeypatch.setattr(update, "tcping", fake_tcping)

        async def fake_speed(candidates, timeout, process_buffer, workers, min_speed,
                             verbose, json_progress=False,
                             speed_bytes=update.DEFAULT_SPEED_BYTES,
                             engine="curl", proxy="", speed_url="", speed_port=0,
                             speed_samples=1):
            return [update.SpeedResult(c.node, c.latency_ms, 50.0, True)
                    for c in candidates]

        monkeypatch.setattr(update, "run_speed_tests", fake_speed)
        async def _no_verify(results, config):
            return results
        monkeypatch.setattr(update, "verify_best_results", _no_verify)
        async def _no_benchmark(config):
            return None
        monkeypatch.setattr(update, "benchmark_network", _no_benchmark)

        cfg = update.AppConfig(
            input_file=Path("ips.txt"), full_output_file=Path("full_ips.txt"),
            best_output_file=Path("best_ips.txt"),
            tcp_timeout=0.5, tcp_workers=2, verbose=False, json_progress=True,
        )
        rc = await update.run(cfg)
        assert rc == 0
        assert used["url"] == update.DEFAULT_INPUT_URL

    @pytest.mark.asyncio
    async def test_benchmark_included(self, sandbox, monkeypatch, capsys):
        async def fake_tcping(node, timeout, samples=1):
            return 10.0

        monkeypatch.setattr(update, "tcping", fake_tcping)

        async def fake_benchmark(config):
            return (12.3, 45.6)

        monkeypatch.setattr(update, "benchmark_network", fake_benchmark)

        async def fake_speed(candidates, timeout, process_buffer, workers, min_speed,
                             verbose, json_progress=False,
                             speed_bytes=update.DEFAULT_SPEED_BYTES,
                             engine="curl", proxy="", speed_url="", speed_port=0,
                             speed_samples=1):
            return [update.SpeedResult(c.node, c.latency_ms, 50.0, True)
                    for c in candidates]

        monkeypatch.setattr(update, "run_speed_tests", fake_speed)
        async def _no_verify(results, config):
            return results
        monkeypatch.setattr(update, "verify_best_results", _no_verify)

        cfg = update.AppConfig(
            input_file=Path("ips.txt"), full_output_file=Path("full_ips.txt"),
            best_output_file=Path("best_ips.txt"),
            tcp_timeout=0.5, tcp_workers=2, verbose=False, json_progress=True,
            input_explicit=True,
        )
        rc = await update.run(cfg)
        assert rc == 0
        assert "Baseline" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_export_formats(self, sandbox, monkeypatch):
        async def fake_tcping(node, timeout, samples=1):
            return 10.0

        monkeypatch.setattr(update, "tcping", fake_tcping)

        async def fake_speed(candidates, timeout, process_buffer, workers, min_speed,
                             verbose, json_progress=False,
                             speed_bytes=update.DEFAULT_SPEED_BYTES,
                             engine="curl", proxy="", speed_url="", speed_port=0,
                             speed_samples=1):
            return [update.SpeedResult(c.node, c.latency_ms, 50.0, True)
                    for c in candidates]

        monkeypatch.setattr(update, "run_speed_tests", fake_speed)
        async def _no_verify(results, config):
            return results
        monkeypatch.setattr(update, "verify_best_results", _no_verify)
        async def _no_benchmark(config):
            return None
        monkeypatch.setattr(update, "benchmark_network", _no_benchmark)

        cfg = update.AppConfig(
            input_file=Path("ips.txt"), full_output_file=Path("full_ips.txt"),
            best_output_file=Path("best_ips.txt"),
            tcp_timeout=0.5, tcp_workers=2, verbose=False, json_progress=True,
            input_explicit=True, export_formats=("v2ray", "clash", "csv"),
        )
        rc = await update.run(cfg)
        assert rc == 0
        assert Path("full_ips.v2ray.txt").exists()
        assert Path("full_ips.clash.yaml").exists()
        assert Path("full_ips.csv").exists()

    @pytest.mark.asyncio
    async def test_ports_expansion(self, sandbox, monkeypatch):
        async def fake_tcping(node, timeout, samples=1):
            return 10.0

        monkeypatch.setattr(update, "tcping", fake_tcping)

        seen = []

        async def fake_speed(candidates, timeout, process_buffer, workers, min_speed,
                             verbose, json_progress=False,
                             speed_bytes=update.DEFAULT_SPEED_BYTES,
                             engine="curl", proxy="", speed_url="", speed_port=0,
                             speed_samples=1):
            seen.extend(c.node.raw for c in candidates)
            return [update.SpeedResult(c.node, c.latency_ms, 50.0, True)
                    for c in candidates]

        monkeypatch.setattr(update, "run_speed_tests", fake_speed)
        async def _no_verify(results, config):
            return results
        monkeypatch.setattr(update, "verify_best_results", _no_verify)
        async def _no_benchmark(config):
            return None
        monkeypatch.setattr(update, "benchmark_network", _no_benchmark)

        cfg = update.AppConfig(
            input_file=Path("ips.txt"), full_output_file=Path("full_ips.txt"),
            best_output_file=Path("best_ips.txt"),
            tcp_timeout=0.5, tcp_workers=2, verbose=False, json_progress=True,
            input_explicit=True, ports=(443, 2053),
        )
        rc = await update.run(cfg)
        assert rc == 0
        # 每个可达 IP 扩展出 2053 端口
        assert any(":2053#" in r for r in seen)
