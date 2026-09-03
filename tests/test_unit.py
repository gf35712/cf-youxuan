# -*- coding: utf-8 -*-
"""核心单元测试：解析、测速解析、候选选择、结果导出、缓存、参数解析。"""
import ipaddress
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import update  # noqa: E402


def _node(ip="1.1.1.1", port=443, region="A"):
    return update.Node(ip, port, region)


# ---------------------------------------------------------------------------
# 节点解析
# ---------------------------------------------------------------------------
class TestParseNode:
    def test_ipv4(self):
        n = update.parse_node("1.1.1.1:443#HK")
        assert n is not None
        assert n.ip == "1.1.1.1" and n.port == 443 and n.region == "HK"

    def test_ipv6(self):
        n = update.parse_node("[2606:4700::1111]:443#HK")
        assert n is not None and n.ip == "2606:4700::1111" and n.port == 443

    def test_invalid(self):
        assert update.parse_node("") is None
        assert update.parse_node("#comment") is None
        assert update.parse_node("1.1.1.1:99999#A") is None  # 端口越界
        assert update.parse_node("999.1.1.1:443#A") is None  # IP 非法
        assert update.parse_node("bad") is None
        assert update.parse_node("1.1.1.1:abc") is None

    def test_no_region_and_bare_ip(self):
        """兼容无 #region 的 `ip:port` 与纯 IP 格式（第三方列表常见）。"""
        n = update.parse_node("1.1.1.1:443")
        assert n is not None and n.region == ""
        n2 = update.parse_node("1.2.3.4:8443")
        assert n2 is not None and n2.port == 8443 and n2.region == ""
        n3 = update.parse_node("5.6.7.8")  # 纯 IP -> 默认 443
        assert n3 is not None and n3.port == 443 and n3.region == ""
        n4 = update.parse_node("2606:4700::1")  # 裸 IPv6 -> 默认 443
        assert n4 is not None and n4.port == 443 and n4.region == ""

    def test_none_input(self):
        """None 输入不崩溃（回归）。"""
        assert update.parse_node(None) is None
        assert update._parse_ports(None) == ()
        assert update._parse_speed_url(None) == (update.SPEED_DOMAIN, update.SPEED_PATH, 0)

    def test_raw(self):
        n = _node()
        assert n.raw == "1.1.1.1:443#A"
        n6 = update.Node("2606:4700::1111", 443, "A")
        assert n6.raw == "[2606:4700::1111]:443#A"

    def test_benchmark_node(self):
        assert update.parse_benchmark_node("1.1.1.1:443") is not None
        assert update.parse_benchmark_node("1.1.1.1:443#HK") is not None
        assert update.parse_benchmark_node("") is None
        assert update.parse_benchmark_node("bad") is None

    def test_load_nodes_dedup(self, tmp_path):
        p = tmp_path / "ips.txt"
        p.write_text("1.1.1.1:443#A\n1.1.1.1:443#A\n2.2.2.2:443#B\n", encoding="utf-8")
        nodes = update.load_nodes(p)
        assert len(nodes) == 2
        with pytest.raises(FileNotFoundError):
            update.load_nodes(tmp_path / "nope.txt")


# ---------------------------------------------------------------------------
# URL / 端口解析
# ---------------------------------------------------------------------------
class TestParseUrl:
    def test_speed_url_default(self):
        assert update._parse_speed_url("") == (update.SPEED_DOMAIN, update.SPEED_PATH, 0)
        assert update._parse_speed_url(None) == (update.SPEED_DOMAIN, update.SPEED_PATH, 0)

    def test_speed_url_custom(self):
        assert update._parse_speed_url("https://speed.example.com:8443/__down") == \
            ("speed.example.com", "/__down", 8443)

    def test_speed_url_without_scheme(self):
        assert update._parse_speed_url("cf.090227.xyz/__down?bytes=99999999") == \
            ("cf.090227.xyz", "/__down", 0)
        assert update._display_speed_url("cf.090227.xyz/__down") == \
            "https://cf.090227.xyz/__down"

    def test_speed_url_invalid(self):
        assert update._parse_speed_url("not a url") == (update.SPEED_DOMAIN, update.SPEED_PATH, 0)

    def test_parse_ports(self):
        assert update._parse_ports("") == ()
        assert update._parse_ports("443,2053,2087") == (443, 2053, 2087)
        assert update._parse_ports("443,443") == (443,)
        assert update._parse_ports("443,abc,99999") == (443,)


# ---------------------------------------------------------------------------
# curl 速度解析
# ---------------------------------------------------------------------------
class TestParseCurlSpeed:
    def test_normal(self):
        # 2MB in (1.0 - 0.5) = 0.5s -> 33.55 Mbps
        assert update.parse_curl_speed("2097152 1.0 0.5") == pytest.approx(33.55, abs=0.01)

    def test_no_starttransfer(self):
        # 无 starttransfer 时用 time_total
        assert update.parse_curl_speed("1048576 1.0") == pytest.approx(8.39, abs=0.01)

    def test_invalid(self):
        assert update.parse_curl_speed("") == 0.0
        assert update.parse_curl_speed("abc") == 0.0
        assert update.parse_curl_speed("0 1 0.5") == 0.0  # size 0
        assert update.parse_curl_speed("100 0 0") == 0.0  # time 0


# ---------------------------------------------------------------------------
# 候选选择 / 过滤
# ---------------------------------------------------------------------------
class TestSelectCandidates:
    def test_top_per_region(self):
        results = [
            update.TcpResult(_node("1.1.1.1", 443, "HK"), 10.0),
            update.TcpResult(_node("2.2.2.2", 443, "HK"), 5.0),
            update.TcpResult(_node("3.3.3.3", 443, "HK"), 20.0),
            update.TcpResult(_node("4.4.4.4", 443, "SG"), 8.0),
        ]
        cands = update.select_candidates(results, 2)
        assert len(cands) == 3  # HK 保留 2 个 + SG 1 个
        hk = [c for c in cands if c.node.region == "HK"]
        assert sorted(hk, key=lambda c: c.latency_ms)[:2] == hk[:2]
        assert {c.node.ip for c in hk} == {"1.1.1.1", "2.2.2.2"}  # 最低延迟保留

    def test_global_mode_caps_total_candidates(self):
        results = [
            update.TcpResult(_node(f"192.0.2.{i}", 443, "R"), float(i))
            for i in range(1, 8)
        ]
        out = update.select_candidates(results, 10, mode="global", global_limit=3)
        assert [r.latency_ms for r in out] == [1.0, 2.0, 3.0]

    def test_adaptive_mode_deduplicates_regional_and_global(self):
        results = [
            update.TcpResult(_node("192.0.2.1", 443, "HK"), 1.0),
            update.TcpResult(_node("192.0.2.2", 443, "HK"), 2.0),
            update.TcpResult(_node("192.0.2.3", 443, "HK"), 3.0),
            update.TcpResult(_node("192.0.2.4", 443, "SG"), 4.0),
        ]
        out = update.select_candidates(results, 10, mode="adaptive", global_limit=2)
        assert len(out) == 4
        assert {r.node.ip for r in out} == {f"192.0.2.{i}" for i in range(1, 5)}

    def test_adaptive_mode_respects_configured_regional_width(self):
        results = [
            update.TcpResult(_node(f"192.0.2.{i}", 443, "HK"), float(i))
            for i in range(1, 13)
        ]
        out = update.select_candidates(results, 10, mode="adaptive", global_limit=1)
        # 全局第 1 个已包含在 HK 的前 10 个中，去重后仍是 10 个；
        # 关键是不能再被旧逻辑硬压成最多 3 个。
        assert len(out) == 10
        assert {r.node.ip for r in out} == {f"192.0.2.{i}" for i in range(1, 11)}

    def test_empty(self):
        assert update.select_candidates([], 10) == []

    def test_filter_fast(self):
        results = [
            update.SpeedResult(_node(), 10.0, 50.0, True),
            update.SpeedResult(_node("2.2.2.2"), 20.0, 5.0, False),
        ]
        fast = update.filter_fast_results(results)
        assert len(fast) == 1 and fast[0].node.ip == "1.1.1.1"


# ---------------------------------------------------------------------------
# 结果写入 / 导出
# ---------------------------------------------------------------------------
class TestResults:
    def _results(self):
        return [
            update.SpeedResult(_node("1.1.1.1", 443, "HK"), 10.0, 50.0, True),
            update.SpeedResult(_node("2.2.2.2", 443, "SG"), 20.0, 5.0, False),
        ]

    def test_write_results(self, tmp_path):
        p = tmp_path / "out.txt"
        update.write_results(p, self._results(), sort="speed")
        content = p.read_text(encoding="utf-8")
        assert "1.1.1.1:443#HK [优选高速 10.0ms | 50.0Mbps]" in content
        assert "2.2.2.2:443#SG [20.0ms | 5.0Mbps]" in content

    def test_write_results_region_filter(self, tmp_path):
        p = tmp_path / "out.txt"
        update.write_results(p, self._results(), regions=("HK",))
        assert "1.1.1.1" in p.read_text(encoding="utf-8")
        assert "2.2.2.2" not in p.read_text(encoding="utf-8")

    def test_export_csv(self, tmp_path):
        p = tmp_path / "out.csv"
        update.export_csv(p, self._results())
        content = p.read_text(encoding="utf-8-sig")
        assert "ip,port,region,latency_ms,speed_mbps,is_fast" in content
        assert "1.1.1.1,443,HK,10.0,50.00,1" in content

    def test_export_v2ray(self, tmp_path):
        p = tmp_path / "out.v2ray.txt"
        update.export_v2ray(p, self._results())
        lines = p.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert lines[0].startswith("vmess://")

    def test_export_singbox(self, tmp_path):
        p = tmp_path / "out.singbox.json"
        update.export_singbox(p, self._results())
        lines = p.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        import json
        obj = json.loads(lines[0])
        assert obj["type"] == "vmess"
        assert obj["server"] == "1.1.1.1"

    def test_export_clash(self, tmp_path):
        p = tmp_path / "out.clash.yaml"
        update.export_clash(p, self._results())
        content = p.read_text(encoding="utf-8")
        assert content.startswith("proxies:")
        assert 'name: "CF-HK-50M"' in content
        assert "server: 1.1.1.1" in content


# ---------------------------------------------------------------------------
# TCP 缓存
# ---------------------------------------------------------------------------
class TestTcpCache:
    def test_roundtrip(self, tmp_path):
        p = tmp_path / "cache.json"
        results = [update.TcpResult(_node(), 12.5)]
        update.save_tcp_cache(p, results)
        loaded = update.load_tcp_cache(p)
        assert "1.1.1.1:443#A" in loaded
        assert loaded["1.1.1.1:443#A"].latency_ms == 12.5

    def test_reject_non_http_scheme(self, tmp_path):
        """漏洞回归：file:// 任意文件读取 + 非 http/https 协议拒绝。"""
        dest = tmp_path / "out.txt"
        assert update.refresh_input_file("file:///etc/passwd", dest, 5) is False
        assert update.refresh_input_file("ftp://example.com/x", dest, 5) is False
        assert update.refresh_input_file("data:text/plain,hello", dest, 5) is False
        assert not dest.exists()

    def test_speed_url_scheme_limited(self):
        """测速 URL 只允许 http/https。"""
        assert update._parse_speed_url("file:///etc/passwd") == (update.SPEED_DOMAIN, update.SPEED_PATH, 0)
        assert update._parse_speed_url("ftp://x/y") == (update.SPEED_DOMAIN, update.SPEED_PATH, 0)
        assert update._parse_speed_url("https://x/y")[0] == "x"
        assert update._parse_speed_url("http://x/y")[0] == "x"

    def test_load_corrupt(self, tmp_path):
        p = tmp_path / "cache.json"
        p.write_text("{bad", encoding="utf-8")
        assert update.load_tcp_cache(p) == {}
        assert update.load_tcp_cache(tmp_path / "nope.json") == {}

    def test_auto_speed_http_error_closes_response(self, monkeypatch):
        """自动测速源探测遇到 HTTPError 时必须关闭响应体。"""
        import io
        from urllib.error import HTTPError

        bodies = []

        def fake_urlopen(*args, **kwargs):
            body = io.BytesIO(b"error")
            bodies.append(body)
            raise HTTPError("https://speed.test/", 530, "origin error", {}, body)

        monkeypatch.setattr(update.urllib.request, "urlopen", fake_urlopen)
        assert update._auto_detect_speed_url(timeout=0.01, probe_bytes=1) == ""
        assert bodies and all(body.closed for body in bodies)

    def test_annotate_waits_between_batches(self, tmp_path, monkeypatch):
        """地区标注超过一批时应真正调用同步 sleep 限速。"""
        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, _limit):
                return self.payload

        sleeps = []

        def fake_urlopen(req, timeout):
            batch = json.loads(req.data.decode("utf-8"))
            payload = json.dumps([
                {"status": "success", "countryCode": "US", "query": item["query"]}
                for item in batch
            ]).encode("utf-8")
            return FakeResponse(payload)

        results = [update.TcpResult(_node(f"192.0.2.{i}", region=""), 10.0) for i in range(1, 52)]
        monkeypatch.setattr(update.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(update.time, "sleep", lambda seconds: sleeps.append(seconds))

        out = update.annotate_regions(results, tmp_path / "annotate.json")
        assert len(out) == 51
        assert all(item.node.region == "US" for item in out)
        assert sleeps == [1.5]


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------
class TestParseArgs:
    def test_defaults(self):
        cfg = update.parse_args([])
        assert cfg.tcp_timeout == update.DEFAULT_TCP_TIMEOUT
        assert cfg.tcp_workers == update.DEFAULT_TCP_WORKERS
        assert cfg.min_speed_mbps == update.DEFAULT_MIN_SPEED_MBPS
        assert cfg.export_formats == ()

    def test_custom(self):
        cfg = update.parse_args(["--tcp-timeout", "2", "--tcp-workers", "100",
                                 "--min-speed", "20", "--top", "5",
                                 "--region", "HK,SG", "--sort", "latency"])
        assert cfg.tcp_timeout == 2.0
        assert cfg.tcp_workers == 100
        assert cfg.min_speed_mbps == 20
        assert cfg.top_per_region == 5
        assert cfg.regions == ("HK", "SG")
        assert cfg.sort == "latency"

    def test_separate_proxy_options(self):
        cfg = update.parse_args([
            "--input-proxy", "http://127.0.0.1:7890",
            "--speed-proxy", "http://127.0.0.1:7891",
        ])
        assert cfg.proxy == ""
        assert cfg.input_proxy == "http://127.0.0.1:7890"
        assert cfg.speed_proxy == "http://127.0.0.1:7891"

    def test_export_repeated(self):
        cfg = update.parse_args(["--export", "v2ray", "--export", "clash"])
        assert cfg.export_formats == ("v2ray", "clash")
        cfg2 = update.parse_args(["--export", "v2ray", "clash"])
        assert cfg2.export_formats == ("v2ray", "clash")

    def test_invalid(self):
        with pytest.raises(SystemExit):
            update.parse_args(["--tcp-timeout", "0"])
        with pytest.raises(SystemExit):
            update.parse_args(["--tcp-workers", "0"])
        with pytest.raises(SystemExit):
            update.parse_args(["--speed-port", "99999"])

    def test_input_explicit(self):
        cfg = update.parse_args(["-i", "myfile.txt"])
        assert cfg.input_explicit is True
        cfg2 = update.parse_args([])
        assert cfg2.input_explicit is False

    def test_recheck_defaults_and_custom(self):
        cfg = update.parse_args([])
        assert cfg.recheck_top == 0 and cfg.recheck_samples == 3
        cfg2 = update.parse_args(["--recheck-top", "50", "--recheck-samples", "5"])
        assert cfg2.recheck_top == 50 and cfg2.recheck_samples == 5

    def test_benchmark_verify(self):
        cfg = update.parse_args(["--no-benchmark", "--verify-top", "0"])
        assert cfg.benchmark_enabled is False
        assert cfg.verify_top == 0

    def test_port_validator(self):
        assert update.parse_args(["--speed-port", "65535"]).speed_port == 65535
        assert update.parse_args(["--speed-port", "0"]).speed_port == 0
        with pytest.raises(SystemExit):
            update.parse_args(["--speed-port", "-1"])


# ---------------------------------------------------------------------------
# sort key
# ---------------------------------------------------------------------------
class TestSortKey:
    def test_speed(self):
        a = update.SpeedResult(_node("1.1.1.1"), 10.0, 50.0, True)
        b = update.SpeedResult(_node("2.2.2.2"), 20.0, 80.0, True)
        # speed 降序：80Mbps 的 key 更小（-80），排序时靠前
        assert update._sort_key(b, "speed") < update._sort_key(a, "speed")

    def test_score_sort(self):
        """综合评分：速度为主、延迟为辅。"""
        r1 = update.SpeedResult(_node("1.1.1.1"), 200.0, 20.0, True)  # 高延迟低速度
        r2 = update.SpeedResult(_node("2.2.2.2"), 50.0, 15.0, True)   # 低延迟中速度
        r3 = update.SpeedResult(_node("3.3.3.3"), 100.0, 30.0, True)  # 高速中延迟
        ordered = sorted([r1, r2, r3], key=lambda r: update._sort_key(r, "score"))
        assert [r.node.ip for r in ordered] == ["3.3.3.3", "1.1.1.1", "2.2.2.2"]
        # score: r3=29, r1=18, r2=14.5

    def test_unknown_fallback(self, capsys):
        r = update.SpeedResult(_node(), 10.0, 50.0, True)
        update._sort_key(r, "bogus")
        assert "falling back" in capsys.readouterr().out


class TestPacketLossOptimization:
    def test_candidate_filter_excludes_high_loss(self):
        results = [
            update.TcpResult(_node("192.0.2.1", 443, "HK"), 10.0, packet_loss=0.0),
            update.TcpResult(_node("192.0.2.2", 443, "HK"), 8.0, packet_loss=25.0),
            update.TcpResult(_node("192.0.2.3", 443, "HK"), 12.0, packet_loss=10.0),
        ]
        out = update.select_candidates(results, 10, max_packet_loss=20.0)
        assert [r.node.ip for r in out] == ["192.0.2.1", "192.0.2.3"]

    def test_default_packet_loss_filter_keeps_legacy_behavior(self):
        result = update.TcpResult(_node("192.0.2.4"), 8.0, packet_loss=100.0)
        assert update.select_candidates([result], 1) == [result]

    def test_packet_loss_arg_is_clamped(self):
        cfg = update.parse_args(["--max-packet-loss", "120"])
        assert cfg.max_packet_loss == 100.0
        cfg2 = update.parse_args(["--max-packet-loss", "20.5"])
        assert cfg2.max_packet_loss == 20.5
