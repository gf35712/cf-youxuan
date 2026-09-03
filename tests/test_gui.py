# -*- coding: utf-8 -*-
"""GUI 纯函数测试：命令构建、进度解析、结果统计、主题、配置、IP 定位。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import gui  # noqa: E402


class TestParseProgressLine:
    def test_json(self):
        r = gui.parse_progress_line('{"progress": 0.5, "stage": "TCP 延迟测试...", "done": 5, "total": 10}')
        assert r is not None
        assert r[0] == 0.5 and r[2] == 5 and r[3] == 10

    def test_tqdm(self):
        r = gui.parse_progress_line("45%|####| 12/34")
        assert r is not None
        assert r[0] == pytest.approx(0.45)
        assert r[2] == 12 and r[3] == 34

    def test_none(self):
        assert gui.parse_progress_line("random line") is None
        assert gui.parse_progress_line("") is None


class TestProgressStage:
    def test_stage1(self):
        v, t = gui.progress_stage("Stage 1/2: TCP latency test, concurrency=500")
        assert v == 0.02 and "TCP" in t

    def test_stage2(self):
        v, t = gui.progress_stage("Stage 2/2: download speed test")
        assert v == 0.45 and "下载" in t

    def test_done(self):
        v, t = gui.progress_stage("Done")
        assert v == 1.0

    def test_none(self):
        assert gui.progress_stage("hello") is None

    def test_scan_mode_descriptions_warn_against_cross_mode_comparison(self):
        assert "TCP 握手" in gui.SCAN_DESCRIPTIONS["tcping"]
        assert "不要直接横比" in gui.SCAN_DESCRIPTIONS["httping"]


class TestBuildUpdateCommand:
    def _base(self):
        return {
            "tcp_timeout": 1.5, "tcp_workers": 500.0,
            "speed_timeout": 6.0, "speed_workers": 16.0,
            "min_speed": 8.0, "top_per_region": 10.0,
        }

    def test_basic(self):
        cmd = gui.build_update_command(Path("update.exe"), Path("wd"), "u", self._base(), False)
        assert cmd[0] == "update.exe"
        assert "--json-progress" in cmd
        assert "--verbose" not in cmd
        assert cmd[cmd.index("--tcp-workers") + 1] == "500"

    def test_verbose(self):
        cmd = gui.build_update_command(Path("update.exe"), Path("wd"), "u", self._base(), True)
        assert "--verbose" in cmd

    def test_speed_bytes_passthrough(self):
        cmd = gui.build_update_command(Path("update.exe"), Path("wd"), "u",
                                       {**self._base(), "speed_bytes": 1048576}, False)
        assert cmd[cmd.index("--speed-bytes") + 1] == "1048576"

    def test_min_speed_mbps(self):
        """最低高速 Mbps 直接透传（不再 ×8）。"""
        cmd = gui.build_update_command(Path("update.exe"), Path("wd"), "u",
                                       {**self._base(), "min_speed": 5.0}, False)
        assert cmd[cmd.index("--min-speed") + 1] == "5.0"

    def test_tcp_timeout_zero_omitted(self):
        cmd = gui.build_update_command(Path("update.exe"), Path("wd"), "u",
                                       {**self._base(), "tcp_timeout": 0}, False)
        assert "--tcp-timeout" not in cmd

    def test_tcp_timeout_is_clamped_for_large_scans(self):
        cmd = gui.build_update_command(Path("update.exe"), Path("wd"), "u",
                                       {**self._base(), "tcp_timeout": 0.5}, False)
        assert cmd[cmd.index("--tcp-timeout") + 1] == "1.0"

    def test_process_completed_requires_done_marker(self):
        assert gui.process_completed(0, True) is True
        assert gui.process_completed(0, False) is False
        assert gui.process_completed(1, True) is False

    def test_latency_display_is_sampled_but_keeps_first_line(self):
        assert gui.should_display_latency(1, 0.0, 0.0) is True
        assert gui.should_display_latency(2, 0.01, 0.0) is False
        assert gui.should_display_latency(20, 0.01, 0.0) is True
        assert gui.should_display_latency(2, 0.20, 0.0) is True

    def test_advanced_flags(self):
        cmd = gui.build_update_command(Path("update.exe"), Path("wd"), "u", {
            **self._base(), "sort": "latency", "region": "HK,SG",
            "csv_export": True, "export_formats": "v2ray clash",
            "speed_url": "https://speed.example.com/__down",
            "speed_port": 2053, "interval": 120}, False)
        assert cmd[cmd.index("--sort") + 1] == "latency"
        assert cmd[cmd.index("--region") + 1] == "HK,SG"
        assert "--csv" in cmd
        assert cmd.count("--export") == 2
        assert cmd[cmd.index("--speed-url") + 1] == "https://speed.example.com/__down"
        assert cmd[cmd.index("--speed-port") + 1] == "2053"

    def test_gui_proxy_is_input_only_and_speed_proxy_is_explicit(self):
        cmd = gui.build_update_command(Path("update.exe"), Path("wd"), "u", {
            **self._base(), "proxy": "http://127.0.0.1:7890"
        }, False)
        assert "--input-proxy" in cmd
        assert "--speed-proxy" not in cmd
        cmd2 = gui.build_update_command(Path("update.exe"), Path("wd"), "u", {
            **self._base(), "proxy": "http://127.0.0.1:7890",
            "speed_proxy": "http://127.0.0.1:7891"
        }, False)
        assert cmd2[cmd2.index("--input-proxy") + 1] == "http://127.0.0.1:7890"
        assert cmd2[cmd2.index("--speed-proxy") + 1] == "http://127.0.0.1:7891"

    def test_benchmark_verify(self):
        base = self._base()
        cmd = gui.build_update_command(Path("update.exe"), Path("wd"), "u", base, False)
        assert "--benchmark" not in cmd and "--verify-top" not in cmd
        cmd2 = gui.build_update_command(Path("update.exe"), Path("wd"), "u",
                                        {**base, "benchmark": "8.8.8.8:443", "verify_top": 5}, False)
        assert cmd2[cmd2.index("--benchmark") + 1] == "8.8.8.8:443"
        assert cmd2[cmd2.index("--verify-top") + 1] == "5"
        cmd3 = gui.build_update_command(Path("update.exe"), Path("wd"), "u",
                                        {**base, "benchmark_enabled": False}, False)
        assert "--no-benchmark" in cmd3

    def test_recheck_passthrough(self):
        cmd = gui.build_update_command(Path("update.exe"), Path("wd"), "u", {
            **self._base(), "recheck_top": 50, "recheck_samples": 3
        }, False)
        assert cmd[cmd.index("--recheck-top") + 1] == "50"
        assert cmd[cmd.index("--recheck-samples") + 1] == "3"



    def test_packet_loss_passthrough(self):
        cmd = gui.build_update_command(Path("update.exe"), Path("wd"), "u", {
            **self._base(), "max_packet_loss": 20
        }, False)
        assert "--no-cache" in cmd
        assert cmd[cmd.index("--max-packet-loss") + 1] == "20.0"

    def test_gui_always_requests_fresh_tcp_scan(self):
        cmd = gui.build_update_command(Path("update.exe"), Path("wd"), "u", self._base(), False)
        assert cmd.count("--no-cache") == 1

    def test_speed_source_preset_values(self):
        assert gui.speed_source_url("自动选择") == ""
        assert gui.speed_source_url("Cloudflare") == "https://speed.cloudflare.com/__down"
        assert gui.speed_source_url("CM提供") == "https://cf.090227.xyz/__down"
        assert gui.speed_source_url("移动专属") == "https://speed.okl.abrdns.com/__down"
        assert gui.speed_source_url("自定义") is None

    def test_speed_source_preset_detects_current_url(self):
        assert gui.speed_source_preset("") == "自动选择"
        assert gui.speed_source_preset("https://cf.090227.xyz/__down") == "CM提供"
        assert gui.speed_source_preset("https://example.com/file") == "自定义"

    def test_stop_after_fast_passthrough(self):
        cmd = gui.build_update_command(Path("update.exe"), Path("wd"), "u", {
            **self._base(), "stop_after_fast": 10
        }, False)
        assert cmd[cmd.index("--stop-after-fast") + 1] == "10"

    def test_candidate_mode_passthrough(self):
        cmd = gui.build_update_command(Path("update.exe"), Path("wd"), "u", {
            **self._base(), "candidate_mode": "adaptive", "candidate_limit": 50
        }, False)
        assert cmd[cmd.index("--candidate-mode") + 1] == "adaptive"
        assert cmd[cmd.index("--candidate-limit") + 1] == "50"

    def test_source_official4(self):
        cmd = gui.build_update_command(Path("update.exe"), Path("wd"), "u",
                                       {**self._base(), "source_type": "official4"}, False)
        assert cmd[cmd.index("--input-mode") + 1] == "official"
        assert cmd[cmd.index("--official-iptype") + 1] == "4"

    def test_source_official6(self):
        cmd = gui.build_update_command(Path("update.exe"), Path("wd"), "u",
                                       {**self._base(), "source_type": "official6"}, False)
        assert cmd[cmd.index("--official-iptype") + 1] == "6"

    def test_source_file(self):
        cmd = gui.build_update_command(Path("update.exe"), Path("wd"), "u",
                                       {**self._base(), "source_type": "file",
                                        "local_file": "D:/x/ips.txt"}, False)
        assert cmd[cmd.index("-i") + 1] == "D:/x/ips.txt"

    def test_source_file_empty_omits_dash_i(self):
        cmd = gui.build_update_command(Path("update.exe"), Path("wd"), "u",
                                       {**self._base(), "source_type": "file",
                                        "local_file": ""}, False)
        assert "-i" not in cmd

    def test_source_url_default(self):
        cmd = gui.build_update_command(Path("update.exe"), Path("wd"), "u",
                                       self._base(), False)
        assert "--input-mode" not in cmd
        assert "-i" not in cmd
    def test_scan_mode_httping_annotate(self):
        cmd = gui.build_update_command(Path("update.exe"), Path("wd"), "u",
                                       {**self._base(), "scan_mode": "httping",
                                        "annotate": True}, False)
        assert cmd[cmd.index("--scan-mode") + 1] == "httping"
        assert "--annotate" in cmd

    def test_scan_mode_default_omits_flags(self):
        cmd = gui.build_update_command(Path("update.exe"), Path("wd"), "u",
                                       {**self._base(), "scan_mode": "tcping",
                                        "annotate": False}, False)
        assert "--scan-mode" not in cmd
        assert "--annotate" not in cmd

    def test_split_node(self):
        assert gui.App._split_node("1.2.3.4:443") == ("1.2.3.4", 443)
        assert gui.App._split_node("[2606:4700::1111]:443") == ("2606:4700::1111", 443)

class TestPickFilter:
    def _rows(self):
        return [
            ("1.1.1.1:443", "HK", 10.0, 80.0, True),
            ("2.2.2.2:443", "US", 150.0, 30.0, True),
            ("3.3.3.3:443", "JP", 200.0, 5.0, False),
            ("4.4.4.4:443", "HK", 50.0, 12.0, False),
        ]

    def test_region_filter(self):
        out = gui.filter_pick_rows(self._rows(), region="HK")
        assert len(out) == 2
        assert all(r[1] == "HK" for r in out)

    def test_region_all(self):
        out = gui.filter_pick_rows(self._rows(), region="全部")
        assert len(out) == 4

    def test_max_latency(self):
        out = gui.filter_pick_rows(self._rows(), maxlat=100.0)
        assert len(out) == 2

    def test_min_speed(self):
        out = gui.filter_pick_rows(self._rows(), minspeed=20.0)
        assert len(out) == 2

    def test_combined(self):
        out = gui.filter_pick_rows(self._rows(), region="HK", maxlat=100.0,
                                   minspeed=20.0, only_fast=True)
        assert len(out) == 1
        assert out[0][0] == "1.1.1.1:443"

    def test_only_fast(self):
        out = gui.filter_pick_rows(self._rows(), only_fast=True)
        assert len(out) == 2
        assert all(r[4] for r in out)

    def test_sort_speed(self):
        out = gui.sort_pick_rows(self._rows(), col="speed", reverse=True)
        assert out[0][3] == 80.0
        assert out[-1][3] == 5.0

    def test_sort_latency(self):
        out = gui.sort_pick_rows(self._rows(), col="latency", reverse=False)
        assert out[0][2] == 10.0

    def test_safe_float(self):
        assert gui._safe_float("") is None
        assert gui._safe_float(" 12.5 ") == 12.5
        assert gui._safe_float("abc") is None

class TestRegionLabel:
    def test_known(self):
        assert gui.region_label("US") == "美国(US)"
        assert gui.region_label("hk") == "香港(HK)"  # 大小写不敏感

    def test_unknown(self):
        assert gui.region_label("XX") == "XX(XX)"

    def test_empty(self):
        assert gui.region_label("") == "全部地区"
        assert gui.region_label(None) == "全部地区"

    def test_region_options_follow_prefilter(self):
        """地区选项 = 当前非地区条件过滤后的国家集合（联动）。"""
        rows = [
            ("1.1.1.1:443", "US", 50.0, 80.0, True),
            ("2.2.2.2:443", "JP", 150.0, 30.0, True),
            ("3.3.3.3:443", "US", 200.0, 5.0, False),
        ]
        # 速度>=20 -> 剩 US,JP；速度>=40 -> 只剩 US
        pre = gui.filter_pick_rows(rows, region="", minspeed=40.0)
        codes = sorted({r[1] for r in pre if r[1]})
        assert codes == ["US"]
        pre2 = gui.filter_pick_rows(rows, region="", minspeed=20.0)
        assert sorted({r[1] for r in pre2 if r[1]}) == ["JP", "US"]


class TestSummarize:
    def test_summarize_results(self):
        text = ("1.1.1.1:443#A [优选高速 10ms | 50Mbps]\n"
                "2.2.2.2:443#B [优选高速 20ms | 80Mbps]\n"
                "3.3.3.3:443#C [30ms | 5Mbps]\n")
        fast, total, lat, speed = gui.summarize_results(text)
        assert fast == 2 and total == 3
        assert lat == 10.0 and speed == 80.0

    def test_parse_curl_speed_result(self):
        r = gui.parse_curl_speed_result("1.1.1.1:443#A [优选高速 12.3ms | 45.6Mbps]")
        assert r == (12.3, 45.6, True)
        assert gui.parse_curl_speed_result("no match") is None


class TestSortFullResultsByLatency:
    def test_sorts_by_latency_ascending(self):
        content = ("2.2.2.2:443#B [优选高速 20ms | 80Mbps]\n"
                   "1.1.1.1:443#A [优选高速 10ms | 50Mbps]\n"
                   "3.3.3.3:443#C [30ms | 5Mbps]\n")
        out = gui.sort_full_results_by_latency(content)
        lines = out.splitlines()
        assert "1.1.1.1" in lines[0]
        assert "2.2.2.2" in lines[1]
        assert "3.3.3.3" in lines[2]

    def test_stable_for_equal_latency(self):
        content = ("2.2.2.2:443#B [20ms | 80Mbps]\n"
                   "1.1.1.1:443#A [20ms | 50Mbps]\n")
        assert gui.sort_full_results_by_latency(content).splitlines() == content.splitlines()

    def test_unparseable_lines_stay_at_end(self):
        content = ("2.2.2.2:443#B [20ms | 80Mbps]\n"
                   "1.1.1.1:443#A [10ms | 50Mbps]\n"
                   "header line\n")
        lines = gui.sort_full_results_by_latency(content).splitlines()
        assert "1.1.1.1" in lines[0]
        assert lines[-1] == "header line"

    def test_empty(self):
        assert gui.sort_full_results_by_latency("") == ""


class TestSuggestRegions:
    def test_china(self):
        assert "HK" in gui.suggest_regions("中国 山东 临沂 移动")
        assert "KR" in gui.suggest_regions("中国 北京")

    def test_other(self):
        assert gui.suggest_regions("美国 洛杉矶") == "US"
        assert gui.suggest_regions("日本 东京") == "JP"
        assert gui.suggest_regions("新加坡") == "SG"
        assert gui.suggest_regions("香港") == "HK"

    def test_empty(self):
        assert gui.suggest_regions("") == ""
        assert gui.suggest_regions(None) == ""
        assert gui.suggest_regions("Mars Colony") == ""


class TestConfig:
    def test_load_missing_defaults(self, tmp_path):
        cfg = gui.load_config(tmp_path / "nope.json")
        assert cfg["tcp_workers"] == gui.DEFAULT_TCP_WORKERS
        assert cfg["min_speed"] == gui.DEFAULT_MIN_SPEED
        assert cfg["verbose"] is False

    def test_save_load_roundtrip(self, tmp_path):
        p = tmp_path / "cfg.json"
        gui.save_config({"ips_url": "http://x", "tcp_workers": 700, "min_speed": 12.5}, path=p)
        cfg = gui.load_config(p)
        assert cfg["ips_url"] == "http://x"
        assert cfg["tcp_workers"] == 700
        assert cfg["min_speed"] == 12.5

    def test_old_config_marked_v1(self, tmp_path):
        p = tmp_path / "cfg.json"
        p.write_text('{"min_speed": 64}', encoding="utf-8")
        cfg = gui.load_config(p)
        assert cfg["config_version"] == 1

    def test_save_writes_version3(self, tmp_path):
        p = tmp_path / "cfg.json"
        gui.save_config({"min_speed": 25}, path=p)
        cfg = gui.load_config(p)
        assert cfg["config_version"] == 3
        assert cfg["min_speed"] == 25  # Mbps 语义，不再 ×8

    def test_v2_config_min_speed_migrated_to_mbps(self, tmp_path):
        """v2 配置的 min_speed（MB/s）×8 迁移到 Mbps。"""
        p = tmp_path / "cfg.json"
        p.write_text('{"config_version": 2, "min_speed": 8}', encoding="utf-8")
        cfg = gui.load_config(p)
        assert cfg["config_version"] == 3
        assert cfg["min_speed"] == 64  # 8 MB/s = 64 Mbps

    def test_save_unknown_keys_dropped(self, tmp_path):
        p = tmp_path / "cfg.json"
        gui.save_config({"hack": "x", "tcp_workers": 300}, path=p)
        cfg = gui.load_config(p)
        assert "hack" not in cfg
        assert cfg["tcp_workers"] == 300


class TestSafeInt:
    def test_safe_int(self):
        assert gui._safe_int("12") == 12
        assert gui._safe_int("  ") == 0
        assert gui._safe_int("abc", 7) == 7
        assert gui._safe_int(None) == 0


class TestSmoothProgress:
    def test_smooth(self):
        assert gui.smooth_progress(0.5, 0.6) == pytest.approx(0.52)
        assert gui.smooth_progress(0.8, 0.6) == 0.6  # 向下直接跟随
        assert gui.smooth_progress(0.5, 0.4) == 0.4
