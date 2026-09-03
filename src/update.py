# -*- coding: utf-8 -*-
"""Cloudflare 优选 IP 测速工具 — 命令行核心（重建版）。

功能：下载 IP 列表 → TCP 延迟测试 → 下载测速 → 优选高速 IP → 结果输出/导出。
与原版行为兼容：参数、输出格式、JSON 进度行（GUI 友好）。
"""
import argparse
import asyncio
import base64
import csv
import ipaddress
import json
import os
import shutil
import socket
import ssl
import statistics
import subprocess
import sys
import tempfile as _tempfile
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass

from config import (
    _apply_config_defaults, _find_config_path, _nonnegative_float, _nonnegative_int,
    _port, _positive_float, _positive_int, load_config_file,
)
from latency import (
    DEFAULT_TCP_SAMPLES, DEFAULT_TCP_TIMEOUT, HTTPING_DOMAIN, MAX_TCP_SAMPLES,
    _httping_once, _tcping_once, httping, positive_worker_count, tcping, tcping_stats,
)
from exporters import (
    FAST_LABEL, _sort_key, _yaml_str, export_clash, export_csv, export_singbox,
    export_v2ray, write_results,
)
from speed_test import (
    BENCHMARK_SPEED_BYTES, DEFAULT_SPEED_BYTES, SPEED_DOMAIN, SPEED_PATH,
    _measure_speed_native_proxy, _native_download, measure_speed_native,
    measure_speed_with_curl, parse_curl_speed,
)
from input_sources import (
    DEFAULT_INPUT_DOWNLOAD_TIMEOUT, DEFAULT_INPUT_FILE, DEFAULT_INPUT_URL, DEFAULT_INPUT_URLS,
    MAX_INPUT_DOWNLOAD_BYTES, OFFICIAL_PREFIXES_V4_URL, OFFICIAL_PREFIXES_V6_URL,
    _FALLBACK_CF_IPV4_PREFIXES, _FALLBACK_CF_IPV6_PREFIXES,
    _download_with_curl as _input_download_with_curl,
    _fetch_official_prefixes as _input_fetch_official_prefixes,
    generate_official_nodes as _input_generate_official_nodes,
    merge_nodes_from_urls as _input_merge_nodes_from_urls,
    refresh_input_file as _input_refresh_input_file,
)
from models import LatencyStats, Node, SpeedResult, TcpResult
from pathlib import Path
from typing import Sequence

try:
    from tqdm import tqdm
except ImportError:
    # 无 tqdm 时的降级：简单计数（原版依赖 tqdm，这里保证可运行）
    class tqdm:  # type: ignore
        def __init__(self, iterable=None, **kwargs):
            self.n = 0
        def update(self, n=1):
            self.n += n
        def close(self):
            pass
        @staticmethod
        def write(s, file=None):
            print(s)

VERSION = "1.0.0"

# ---- 默认参数（与原版一致）----
# 输出/缓存路径属于主流程，不属于输入源模块。
DEFAULT_FULL_OUTPUT_FILE = Path("full_ips.txt")
DEFAULT_BEST_OUTPUT_FILE = Path("best_ips.txt")
DEFAULT_CACHE_FILE = Path("tcp_cache.json")
DEFAULT_TCP_WORKERS = 200  # TCP 并发（原 500，降低提高稳定性）
SCAN_MODES = ("tcping", "httping")
ANNOTATE_CACHE_FILE = "annotate_cache.json"
DEFAULT_SPEED_TIMEOUT = 6
DEFAULT_SPEED_PROCESS_BUFFER = 8
DEFAULT_SPEED_WORKERS = 8  # 测速并发（原 16，降低减少失败率）
DEFAULT_MIN_SPEED_MBPS = 8
DEFAULT_TOP_PER_REGION = 10
CANDIDATE_MODES = ("regional", "global", "adaptive")
DEFAULT_CANDIDATE_MODE = "regional"
DEFAULT_CANDIDATE_LIMIT = 50
DEFAULT_STOP_AFTER_FAST = 0  # CLI/GUI 默认完整测试候选池；可主动启用早停
DEFAULT_RECHECK_TOP = 0  # CLI 默认不二次复测；GUI 默认复测前 50 个
DEFAULT_RECHECK_SAMPLES = 3
DEFAULT_MAX_PACKET_LOSS = 100.0  # CLI 默认不按丢包率淘汰，保持旧行为

VERIFY_MIN_RATIO = 0.5  # 二次验证速度低于此比例视为失效

_SORT_KEYS = ("speed", "latency", "region", "score")

# 自动测速源：speed_url 为空时按序探测，取第一个可达的（借鉴 CFData-WEB 的 auto 源）
AUTO_SPEED_SOURCES = (
    ("speed.cloudflare.com", "/__down"),
    ("speedtest.cloudflare.com", "/__down"),
)

# ---------------------------------------------------------------------------
# 数据模型（集中于 models.py；此处继续 re-export 兼容旧调用）
# ---------------------------------------------------------------------------


@dataclass
class AppConfig:
    input_file: Path = DEFAULT_INPUT_FILE
    full_output_file: Path = DEFAULT_FULL_OUTPUT_FILE
    best_output_file: Path = DEFAULT_BEST_OUTPUT_FILE
    tcp_timeout: float = DEFAULT_TCP_TIMEOUT
    tcp_workers: int = DEFAULT_TCP_WORKERS
    tcp_samples: int = DEFAULT_TCP_SAMPLES
    speed_timeout: float = DEFAULT_SPEED_TIMEOUT
    speed_process_buffer: float = DEFAULT_SPEED_PROCESS_BUFFER
    speed_workers: int = DEFAULT_SPEED_WORKERS
    min_speed_mbps: float = DEFAULT_MIN_SPEED_MBPS
    top_per_region: int = DEFAULT_TOP_PER_REGION
    candidate_mode: str = DEFAULT_CANDIDATE_MODE
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT
    stop_after_fast: int = DEFAULT_STOP_AFTER_FAST
    recheck_top: int = DEFAULT_RECHECK_TOP
    recheck_samples: int = DEFAULT_RECHECK_SAMPLES
    max_packet_loss: float = DEFAULT_MAX_PACKET_LOSS
    speed_bytes: int = DEFAULT_SPEED_BYTES
    speed_samples: int = 1
    speed_engine: str = "curl"
    ports: tuple = ()
    proxy: str = ""
    input_proxy: str = ""
    speed_proxy: str = ""
    no_cache: bool = False
    verbose: bool = False
    json_progress: bool = False
    sort: str = "speed"
    regions: tuple = ()
    csv_export: bool = False
    export_formats: tuple = ()
    speed_url: str = ""
    speed_port: int = 0
    benchmark: str = "1.1.1.1:443"
    benchmark_enabled: bool = True
    verify_top: int = 0
    input_urls: tuple = ()
    interval_seconds: int = 0
    input_explicit: bool = False
    input_mode: str = "url"  # url (default) / official
    official_iptype: int = 4  # 官方段 IP 类型 4/6
    official_per_prefix: int = 5  # 每段随机采样数
    scan_mode: str = "tcping"  # tcping / httping
    annotate: bool = False  # 地区标注（不筛选）


# ---------------------------------------------------------------------------
# argparse 类型校验（实现集中于 config.py，名称在此兼容导出）
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> AppConfig:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description="Filter IPs by TCP latency and download speed.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--config", type=Path, default=None,
                        help="JSON file with default option values (CLI args take precedence)")
    parser.add_argument("-i", "--input", type=Path, default=DEFAULT_INPUT_FILE, help="input file")
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_FULL_OUTPUT_FILE, help="full output file")
    parser.add_argument("--best-output", type=Path, default=DEFAULT_BEST_OUTPUT_FILE, help="fast IP output file")
    parser.add_argument("--tcp-timeout", type=_positive_float, default=DEFAULT_TCP_TIMEOUT, help="TCP timeout in seconds")
    parser.add_argument("--tcp-workers", type=_positive_int, default=DEFAULT_TCP_WORKERS, help="TCP test concurrency")
    parser.add_argument("--tcp-samples", type=_positive_int, default=DEFAULT_TCP_SAMPLES,
                        help=f"parallel TCP probes per node (>1 takes min latency, max {MAX_TCP_SAMPLES})")
    parser.add_argument("--speed-timeout", type=_positive_float, default=DEFAULT_SPEED_TIMEOUT, help="speed timeout in seconds")
    parser.add_argument("--speed-process-buffer", type=_positive_float, default=DEFAULT_SPEED_PROCESS_BUFFER,
                        help="extra seconds before killing a stuck curl process")
    parser.add_argument("--speed-workers", type=_positive_int, default=DEFAULT_SPEED_WORKERS, help="speed test concurrency")
    parser.add_argument("--min-speed", type=_nonnegative_float, default=DEFAULT_MIN_SPEED_MBPS, help="minimum fast speed in Mbps")
    parser.add_argument("--top", type=_positive_int, default=DEFAULT_TOP_PER_REGION, help="latency candidates kept per region; all input nodes are still TCP-tested, only these candidates are download-tested")
    parser.add_argument("--candidate-mode", choices=list(CANDIDATE_MODES), default=DEFAULT_CANDIDATE_MODE,
                        help="download candidate policy: regional, global, or adaptive")
    parser.add_argument("--candidate-limit", type=_positive_int, default=DEFAULT_CANDIDATE_LIMIT,
                        help="global candidate cap used by global/adaptive mode")
    parser.add_argument("--stop-after-fast", type=_nonnegative_int, default=DEFAULT_STOP_AFTER_FAST,
                        help="stop download tests after this many fast nodes (0 = test all)")
    parser.add_argument("--recheck-top", type=_nonnegative_int, default=DEFAULT_RECHECK_TOP,
                        help="recheck the latency of the top K reachable nodes (0 = disabled)")
    parser.add_argument("--recheck-samples", type=_positive_int, default=DEFAULT_RECHECK_SAMPLES,
                        help="TCP probes per rechecked node (default 3)")
    parser.add_argument("--max-packet-loss", type=_nonnegative_float, default=DEFAULT_MAX_PACKET_LOSS,
                        help="exclude rechecked candidates above this packet-loss percentage (0-100; default 100)")
    parser.add_argument("--speed-bytes", type=_positive_int, default=DEFAULT_SPEED_BYTES,
                        help="bytes to download per speed test (default 4 MiB)")
    parser.add_argument("--speed-samples", type=_positive_int, default=1,
                        help="speed test repetitions per node; median is used (1-8, default 1)")
    parser.add_argument("--ports", default="",
                        help="comma-separated extra ports per IP (e.g. 443,2053,2087)")
    parser.add_argument("--proxy", default="",
                        help="legacy proxy: applies to both input lists and node speed tests")
    parser.add_argument("--input-proxy", default="",
                        help="proxy only for downloading input IP lists; node tests stay direct")
    parser.add_argument("--speed-proxy", default="",
                        help="proxy only for node TCP/download tests; empty means direct")
    parser.add_argument("--speed-engine", choices=["curl", "native"], default="curl",
                        help="speed test engine: curl subprocess (default) or native asyncio TLS")
    parser.add_argument("--no-cache", action="store_true",
                        help="disable TCP result cache (re-test all nodes)")
    parser.add_argument("--verbose", action="store_true", help="print each successful test result")
    parser.add_argument("--json-progress", action="store_true",
                        help="emit machine-readable JSON progress lines (GUI-friendly)")


    parser.add_argument("--csv", action="store_true",
                        help="also export CSV files (full_ips.csv / best_ips.csv)")
    parser.add_argument("--export", default=[], action="append", nargs="+",
                        choices=["csv", "v2ray", "clash", "singbox"],
                        help="extra export formats: csv / v2ray / clash / singbox (repeatable)")
    parser.add_argument("--interval", type=_nonnegative_int, default=0,
                        help="re-run every N seconds (0 = run once); Ctrl+C to stop")
    parser.add_argument("--input-url", nargs="+", action="append", default=[],
                        help="input URL(s) (can be specified multiple times; merge all sources)")
    parser.add_argument("--scan-mode", choices=list(SCAN_MODES), default="tcping",
                        help="latency test mode: tcping (default, TCP handshake) or httping (TTFB)")
    parser.add_argument("--annotate", action="store_true",
                        help="annotate empty regions via ip-api.com (countryCode, cached)")
    parser.add_argument("--input-mode", choices=["url", "official"], default="url",
                        help="input mode: url (default) or official (scan Cloudflare official prefixes)")
    parser.add_argument("--official-iptype", type=int, choices=[4, 6], default=4,
                        help="official mode IP type: 4 (IPv4, default) or 6 (IPv6)")
    parser.add_argument("--official-per-prefix", type=_positive_int, default=5,
                        help="official mode: random IPs sampled per prefix (default 5)")
    parser.add_argument("--benchmark", default="1.1.1.1:443",
                        help="baseline node IP:PORT to measure current network quality")
    parser.add_argument("--no-benchmark", action="store_true",
                        help="skip baseline network test")
    parser.add_argument("--verify-top", type=_nonnegative_int, default=0,
                        help="re-verify top N best IPs (0 disables; default 10)")
    parser.add_argument("--speed-url", default="",
                        help="custom speed-test URL, e.g. https://speed.example.com/__down (default cloudflare)")
    parser.add_argument("--speed-port", type=_port, default=0,
                        help="override speed-test port (0 = use node port)")
    parser.add_argument("--sort", choices=_SORT_KEYS, default="speed",
                        help="result sort key: speed (default), latency, region, or score")
    parser.add_argument("--region", default="",
                        help="comma-separated region filter, e.g. HK,SG (case-insensitive)")


    cfg_path = _find_config_path(argv)
    if cfg_path:
        _apply_config_defaults(parser, cfg_path)

    # -i/--input 是否被显式指定（避免显式本地文件被远程下载覆盖）
    input_explicit = any(
        a in ("-i", "--input") or a.startswith("-i=") or a.startswith("--input=")
        for a in argv
    )
    args = parser.parse_args(argv)
    return AppConfig(
        input_file=args.input,
        full_output_file=args.output,
        best_output_file=args.best_output,
        tcp_timeout=args.tcp_timeout,
        tcp_workers=args.tcp_workers,
        tcp_samples=args.tcp_samples,
        speed_timeout=args.speed_timeout,
        speed_process_buffer=args.speed_process_buffer,
        speed_workers=args.speed_workers,
        min_speed_mbps=args.min_speed,
        top_per_region=args.top,
        candidate_mode=args.candidate_mode,
        candidate_limit=args.candidate_limit,
        stop_after_fast=args.stop_after_fast,
        recheck_top=args.recheck_top,
        recheck_samples=args.recheck_samples,
        max_packet_loss=min(100.0, args.max_packet_loss),
        speed_bytes=args.speed_bytes,
        speed_samples=args.speed_samples,
        speed_engine=args.speed_engine,
        ports=_parse_ports(args.ports),
        proxy=args.proxy,
        input_proxy=args.input_proxy,
        speed_proxy=args.speed_proxy,
        no_cache=args.no_cache,
        verbose=args.verbose,
        json_progress=args.json_progress,
        sort=args.sort,
        regions=tuple(r.strip() for r in args.region.split(",") if r.strip()),
        csv_export=args.csv,
        export_formats=tuple(fmt for batch in args.export for fmt in batch),
        speed_url=args.speed_url,
        speed_port=args.speed_port,
        benchmark=args.benchmark,
        benchmark_enabled=not args.no_benchmark,
        verify_top=args.verify_top,
        input_urls=tuple(u for batch in args.input_url for u in batch),
        interval_seconds=args.interval,
        input_explicit=input_explicit,
        scan_mode=args.scan_mode,
        annotate=args.annotate,
        input_mode=args.input_mode,
        official_iptype=args.official_iptype,
        official_per_prefix=args.official_per_prefix,
    )


# ---------------------------------------------------------------------------
# 节点解析
# ---------------------------------------------------------------------------
def parse_node(line: str) -> Node | None:
    """解析一行输入，兼容三种格式：
      - `ip:port#region`（推荐，带地区标签）
      - `ip:port`（region 为空）
      - `ip`（纯 IP，默认 443 端口）
    也支持 `[ipv6]:port#region`。
    """
    if line is None:
        return None
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    region = ""
    address = text
    if "#" in text:
        address, region = (part.strip() for part in text.split("#", 1))
        if not address or not region:
            return None
    ip: str
    port_text: str
    if address.startswith("["):  # [v6]:port
        end = address.find("]")
        if end == -1 or end + 1 >= len(address) or address[end + 1] != ":":
            return None
        ip = address[1:end]
        port_text = address[end + 2:]
    elif address.count(":") == 0:  # 纯 IPv4（无端口）
        ip, port_text = address, ""
    elif address.count(":") == 1:  # IPv4:port
        ip, port_text = (part.strip() for part in address.rsplit(":", 1))
    else:  # 无方括号的 IPv6（视为纯 IPv6）
        try:
            ipaddress.ip_address(address)
        except ValueError:
            return None
        ip, port_text = address, ""
    if not ip:
        return None
    if port_text:
        try:
            port = int(port_text)
        except ValueError:
            return None
        if not (1 <= port <= 65535):
            return None
    else:
        port = 443  # 纯 IP 默认端口
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return None
    return Node(ip=ip, port=port, region=region)


def parse_benchmark_node(addr: str) -> Node | None:
    """解析基准节点 `ip:port` 或 `[v6]:port`（无 #region）。"""
    text = addr.strip()
    if not text:
        return None
    if "#" in text:  # 兼容 ip:port#region 完整格式
        return parse_node(text)
    ip: str
    port_text: str
    if text.startswith("["):  # [ipv6]:port
        end = text.find("]")
        if end == -1 or end + 1 >= len(text) or text[end + 1] != ":":
            return None
        ip = text[1:end]
        port_text = text[end + 2:]
    elif text.count(":") == 1:
        ip, port_text = (p.strip() for p in text.rsplit(":", 1))
    else:
        return None
    try:
        port = int(port_text)
    except ValueError:
        return None
    if not ip or not (1 <= port <= 65535):
        return None
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return None
    return Node(ip=ip, port=port, region="")


def _parse_speed_url(url: str) -> tuple[str, str, int]:
    """解析测速 URL -> (domain, path, port)。

    兼容用户常写的 `example.com/__down`，默认按 HTTPS 解析。
    """
    from urllib.parse import urlsplit
    if not url:
        return SPEED_DOMAIN, SPEED_PATH, 0
    try:
        raw = str(url).strip()
        if any(char.isspace() for char in raw):
            return SPEED_DOMAIN, SPEED_PATH, 0
        if "://" not in raw and not raw.startswith("//"):
            raw = "https://" + raw
        parts = urlsplit(raw)
        if not parts.scheme or not parts.hostname:
            return SPEED_DOMAIN, SPEED_PATH, 0
        # 测速 URL 只允许 http/https，拒绝 file://、ftp:// 等
        if parts.scheme.lower() not in ("http", "https"):
            return SPEED_DOMAIN, SPEED_PATH, 0
        host = parts.hostname
        path = parts.path or SPEED_PATH
        port = parts.port or 0
        return host, path, port
    except ValueError:
        return SPEED_DOMAIN, SPEED_PATH, 0


def _display_speed_url(url: str) -> str:
    """返回日志中实际使用的测速源地址。"""
    domain, path, port = _parse_speed_url(url)
    port_text = f":{port}" if port else ""
    return f"https://{domain}{port_text}{path}"


def _parse_ports(raw: str) -> tuple[int, ...]:
    """解析 `--ports 443,2053,2087` 为整数元组。"""
    if raw is None:
        return ()
    if not raw.strip():
        return ()
    ports = []
    for p in raw.split(","):
        p = p.strip()
        if p:
            try:
                v = int(p)
                if 1 <= v <= 65535:
                    ports.append(v)
            except ValueError:
                pass
    return tuple(sorted(set(ports)))


def load_nodes(path: Path) -> list[Node]:
    if not path.exists():
        raise FileNotFoundError(f"input file not found: {path}")
    nodes: list[Node] = []
    seen: set[Node] = set()
    with path.open("r", encoding="utf-8-sig") as file:
        for line in file:
            node = parse_node(line)
            if node is None or node in seen:
                continue
            seen.add(node)
            nodes.append(node)
    return nodes


# ---------------------------------------------------------------------------
# TCP 结果缓存
# ---------------------------------------------------------------------------
def load_tcp_cache(path: Path) -> dict[str, TcpResult]:
    """加载上次 TCP 缓存。返回 {node_key: TcpResult}。"""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        out: dict[str, TcpResult] = {}
        for key, val in raw.items():
            try:
                n = Node(val["ip"], val["port"], val.get("region", ""))
                out[key] = TcpResult(node=n, latency_ms=float(val["latency_ms"]),
                                     packet_loss=float(val.get("packet_loss", 0.0)))
            except (KeyError, ValueError, TypeError):
                pass
        return out
    except (json.JSONDecodeError, OSError):
        return {}


def save_tcp_cache(path: Path, results: list[TcpResult]) -> None:
    """保存 TCP 结果到缓存文件（原子写入）。"""
    data = {
        r.node.raw: {"ip": r.node.ip, "port": r.node.port, "region": r.node.region,
                     "latency_ms": r.latency_ms, "packet_loss": r.packet_loss}
        for r in results
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 输入源实现集中于 input_sources.py；以下名称保持旧 API 兼容。
# ---------------------------------------------------------------------------
refresh_input_file = _input_refresh_input_file
_download_with_curl = _input_download_with_curl


def refresh_input_file_with_fallback(url: str, path: Path, timeout: float,
                                     *, skip_existing: bool = False,
                                     proxy: str = "") -> bool:
    """下载输入列表；保留旧 refresh_input_file 测试/调用接缝。"""
    if proxy and refresh_input_file(url, path, timeout,
                                    skip_existing=skip_existing, proxy=proxy):
        return True
    if proxy:
        print("WARNING: input proxy unavailable; retrying input list directly ...")
        return refresh_input_file(url, path, timeout, skip_existing=False, proxy="")
    return refresh_input_file(url, path, timeout,
                              skip_existing=skip_existing, proxy="")


def merge_nodes_from_urls(urls: Sequence[str], path: Path,
                          timeout: float = DEFAULT_INPUT_DOWNLOAD_TIMEOUT,
                          proxy: str = "") -> list[Node]:
    return _input_merge_nodes_from_urls(urls, path, timeout, proxy,
                                        download_fn=refresh_input_file_with_fallback)


def _fetch_official_prefixes(iptype: int, timeout: float) -> tuple[str, ...]:
    return _input_fetch_official_prefixes(iptype, timeout)


def generate_official_nodes(iptype: int = 4, per_prefix: int = 5, port: int = 443,
                            timeout: float = DEFAULT_INPUT_DOWNLOAD_TIMEOUT) -> list[Node]:
    return _input_generate_official_nodes(iptype, per_prefix, port, timeout,
                                          fetch_fn=_fetch_official_prefixes)








def annotate_regions(results: list[TcpResult], cache_path: Path,
                     timeout: float = 10.0) -> list[TcpResult]:
    """对 region 为空的节点用 ip-api.com 批量查询标注地区（countryCode）。

    • 只查询 region 为空的 IP（去重）
    • 使用免费批量接口 ip-api.com/batch，最多 100 个 IP/批
    • 结果缓存到 cache_path，避免重复查询
    • 失败/限速静默保留空 region，不影响主流程
    """
    to_query: list[str] = []
    cache: dict[str, str] = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cache = {}
    for r in results:
        ip = r.node.ip
        if r.node.region:
            continue
        if ip in cache and cache[ip]:
            continue
        if ip not in to_query:
            to_query.append(ip)

    if not to_query:
        return list(results)

    import urllib.request as _ur
    import urllib.error as _uer
    # 分批查询（每批 ≤ 50，间隔 1.5s 防限速）
    batch_size = 50
    batch_api = "http://ip-api.com/batch?fields=status,countryCode,query"
    new_cache: dict[str, str] = {}
    for chunk_start in range(0, len(to_query), batch_size):
        chunk = to_query[chunk_start:chunk_start + batch_size]
        body = json.dumps([{"query": ip, "fields": "status,countryCode,query"}
                           for ip in chunk]).encode("utf-8")
        try:
            req = _ur.Request(batch_api, data=body,
                             headers={"Content-Type": "application/json",
                                      "User-Agent": "cf-ip-updater/1.0"})
            with _ur.urlopen(req, timeout=timeout) as resp:
                raw = json.loads(resp.read(64 * 1024).decode("utf-8", errors="replace"))
            if isinstance(raw, list):
                for item in raw:
                    if item.get("status") == "success":
                        cc = item.get("countryCode", "") or ""
                        qip = item.get("query", "")
                        if qip and cc:
                            new_cache[qip] = cc
        except _uer.HTTPError as exc:
            try:
                exc.close()
            except Exception:
                pass
        except (OSError, _uer.URLError, json.JSONDecodeError, TimeoutError):
            pass
        if chunk_start + batch_size < len(to_query):
            time.sleep(1.5)  # 同步函数中必须真正等待，避免触发免费接口限速

    # 合并缓存
    cache.update(new_cache)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp = cache_path.with_name(cache_path.name + ".tmp")
        temp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        temp.replace(cache_path)
    except OSError:
        pass

    # 重建 TcpResult 并更新 region
    out: list[TcpResult] = []
    for r in results:
        if not r.node.region:
            cc = cache.get(r.node.ip, "")
            if cc:
                out.append(TcpResult(
                    node=Node(r.node.ip, r.node.port, cc),
                    latency_ms=r.latency_ms))
                continue
        out.append(r)
    return out


def _auto_detect_speed_url(timeout: float = 8.0,
                            probe_bytes: int = 512 * 1024) -> str:
    """按序探测内置测速源，返回第一个可达的完整 URL；全部失败返回空串。

    借鉴 CFData-WEB 的 auto 测速源：默认源不可达时自动切换，避免单点源
    抖动导致大面积误杀。只下载 probe_bytes 验证可达性，不写本地文件。
    """
    for domain, path in AUTO_SPEED_SOURCES:
        probe_url = f"https://{domain}{path}?bytes={probe_bytes}"
        try:
            request = urllib.request.Request(
                probe_url, headers={"User-Agent": "cf-ip-updater/1.0"})
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                got = 0
                while got < probe_bytes:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    got += len(chunk)
                if got > 0:
                    return f"https://{domain}{path}"
        except urllib.error.HTTPError as exc:
            # HTTPError 携带响应体；显式关闭，避免 Python 3.14 下 ResourceWarning。
            try:
                exc.close()
            except Exception:
                pass
            continue
        except (OSError, ValueError, urllib.error.URLError):
            continue
    return ""






def get_curl_command() -> str | None:
    """返回 curl 可执行文件路径；找不到返回 None。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        curl_exe = Path(sys._MEIPASS) / "curl.exe"
        if curl_exe.exists():
            return str(curl_exe)
    if sys.platform == "win32":
        found = shutil.which("curl.exe") or shutil.which("curl")
    else:
        found = shutil.which("curl")
    return found


# ---------------------------------------------------------------------------
# TCP 延迟测试
# ---------------------------------------------------------------------------










def emit_progress(json_progress: bool, progress, total: int, stage: str,
                  done: int | None = None) -> None:
    """按需输出 JSON 进度行（GUI 可解析）；非 JSON 模式仅驱动 tqdm。"""
    if json_progress:
        if done is None:
            done = getattr(progress, "n", 0)
        print(json.dumps({
            "progress": round(done / total, 4) if total else 0.0,
            "stage": stage,
            "done": done,
            "total": total,
        }, ensure_ascii=False), flush=True)


async def recheck_latency_results(results: Sequence[TcpResult], timeout: float,
                                  top_k: int, samples: int = DEFAULT_RECHECK_SAMPLES) -> list[TcpResult]:
    """对延迟最好的前 K 个节点复测并记录丢包率。

    复测不能一次性把 ``top_k * samples`` 个连接打满；若复测阶段因瞬时
    拥塞全部失败，也不能把初测成功的节点误标为 100% 丢包。
    """
    if top_k <= 0 or not results:
        return list(results)
    targets = sorted(results, key=_candidate_sort_key)[:top_k]
    semaphore = asyncio.Semaphore(min(8, max(1, len(targets))))

    # 复测使用稍宽松的下限，避免主扫描允许用户设置极短超时时，复测
    # 因同一个过短 timeout 全部失败而污染丢包过滤。
    recheck_timeout = max(float(timeout), 1.0)
    async def check_with_safe_timeout(result: TcpResult) -> tuple[TcpResult, LatencyStats]:
        async with semaphore:
            return result, await tcping_stats(result.node, recheck_timeout, samples)

    checked_pairs = await asyncio.gather(*(check_with_safe_timeout(result) for result in targets))
    checked = [stats for _, stats in checked_pairs]
    by_node: dict[Node, LatencyStats] = {result.node: stats for result, stats in zip(targets, checked)}
    out: list[TcpResult] = []
    failed_rechecks = 0
    for result in results:
        stats = by_node.get(result.node)
        if stats is None:
            out.append(result)
            continue
        if stats.received <= 0:
            failed_rechecks += 1
            out.append(result)
            continue
        out.append(TcpResult(
            node=result.node,
            latency_ms=stats.latency_ms if stats.latency_ms is not None else result.latency_ms,
            packet_loss=stats.packet_loss,
        ))
    tested = len(by_node)
    reliable = [stats for stats in checked if stats.received > 0]
    avg_loss = sum(stats.packet_loss for stats in reliable) / len(reliable) if reliable else 0.0
    suffix = f"; failed rechecks kept: {failed_rechecks}" if failed_rechecks else ""
    print(f"Latency recheck: {tested} nodes, {samples} probes each, "
          f"average loss {avg_loss:.1f}%{suffix}")
    return out


async def run_tcp_tests(nodes: Sequence[Node], timeout: float, workers: int, verbose: bool,
                        json_progress: bool = False, samples: int = 1,
                        mode: str = "tcping") -> list[TcpResult]:
    queue: asyncio.Queue = asyncio.Queue()
    results: list[TcpResult] = []
    progress = tqdm(total=len(nodes), desc="TCP latency", unit="ip",
                    disable=json_progress, file=None if json_progress else sys.stderr)
    emit_progress(json_progress, progress, len(nodes), "TCP 延迟测试...", done=0)
    # 进度节流：每 ~1% 发一条 JSON 进度（GUI 进度条实时更新，避免"摆设"）
    done_count = 0
    step = max(1, len(nodes) // 100) if nodes else 1

    async def worker() -> None:
        nonlocal done_count
        while True:
            node = await queue.get()
            try:
                if node is None:
                    return
                if mode == "httping":
                    latency = await httping(node, timeout, samples)
                else:
                    latency = await tcping(node, timeout, samples)
                if latency is not None:
                    results.append(TcpResult(node=node, latency_ms=latency))
                    if verbose:
                        tqdm.write(f"[LAT] {node.raw} -> {latency} ms")
                done_count += 1
                if done_count % step == 0 or done_count >= len(nodes):
                    emit_progress(json_progress, progress, len(nodes),
                                  "TCP 延迟测试...", done=done_count)
                progress.update(1)
            except Exception as exc:  # noqa: BLE001  # 单点失败不能挂死整个队列
                tqdm.write(f"[LAT] worker error on {node.raw}: {exc}")
            finally:
                # 无论成功/异常都放行 queue.join()
                queue.task_done()

    task_count = positive_worker_count(workers, len(nodes))
    tasks = [asyncio.create_task(worker()) for _ in range(task_count)]
    for node in nodes:
        queue.put_nowait(node)
    for _ in tasks:
        queue.put_nowait(None)
    await queue.join()
    await asyncio.gather(*tasks)
    emit_progress(json_progress, progress, len(nodes), "TCP 延迟测试...", done=done_count)
    progress.close()
    return results


async def recover_failed_tcp_tests(
    nodes: Sequence[Node],
    successful: Sequence[TcpResult],
    timeout: float,
    workers: int,
    verbose: bool,
    json_progress: bool = False,
    mode: str = "tcping",
) -> list[TcpResult]:
    """用较稳参数补测本轮初测失败的节点。

    这不是历史缓存，也不是重复使用旧结果：只针对本轮刚刚失败的节点
    再试一次。它主要防止过短超时或瞬时并发拥塞把整批候选误判为不可达。
    """
    success_keys = {result.node.raw for result in successful}
    retry_nodes = [node for node in nodes if node.raw not in success_keys]
    if not retry_nodes:
        return []
    retry_timeout = max(float(timeout), 1.0)
    retry_workers = max(1, min(int(workers), 64))
    print(f"TCP recovery pass: retrying {len(retry_nodes)} failed nodes, "
          f"timeout={retry_timeout:.1f}s, concurrency={retry_workers}")
    recovered = await run_tcp_tests(
        retry_nodes, retry_timeout, retry_workers, verbose, json_progress,
        samples=1, mode=mode,
    )
    print(f"TCP recovery pass complete: recovered {len(recovered)}/{len(retry_nodes)} nodes")
    return recovered


# ---------------------------------------------------------------------------
# 下载测速（curl 引擎）
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# 下载测速（native asyncio TLS 引擎）
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# 测速调度
# ---------------------------------------------------------------------------
async def run_speed_tests(
    candidates: Sequence[TcpResult],
    timeout: float,
    process_buffer: float,
    workers: int,
    min_speed: float,
    verbose: bool,
    json_progress: bool = False,
    speed_bytes: int = DEFAULT_SPEED_BYTES,
    engine: str = "curl",
    proxy: str = "",
    speed_url: str = "",
    speed_port: int = 0,
    speed_samples: int = 1,
    stop_after_fast: int = DEFAULT_STOP_AFTER_FAST,
) -> list[SpeedResult]:
    # 早停模式只会完成前一小部分任务，优先测试延迟/丢包更好的候选，
    # 提高较早得到高质量节点的概率；完整测速仍保持传入顺序。
    if stop_after_fast > 0:
        candidates = sorted(candidates, key=_candidate_sort_key)
    queue: asyncio.Queue = asyncio.Queue()
    results: list[SpeedResult] = []
    progress = tqdm(total=len(candidates), desc="Download speed", unit="ip",
                    disable=json_progress, file=None if json_progress else sys.stderr)
    emit_progress(json_progress, progress, len(candidates), "下载速度测试...", done=0)
    done_count = 0
    fast_count = 0
    stop_requested = False
    error_count = 0
    step = max(1, len(candidates) // 100) if candidates else 1

    async def worker() -> None:
        nonlocal done_count, fast_count, stop_requested, error_count
        while True:
            candidate = await queue.get()
            try:
                if candidate is None:
                    return
                if stop_requested:
                    continue
                n_samples = max(1, min(speed_samples, 8))
                speeds = []
                for _ in range(n_samples):
                    if engine == "native":
                        spd = await measure_speed_native(candidate.node, timeout, speed_bytes,
                                                         proxy, speed_url, speed_port)
                    else:
                        spd = await asyncio.to_thread(
                            measure_speed_with_curl, candidate.node, timeout, process_buffer,
                            speed_bytes, proxy, speed_url, speed_port,
                        )
                    if spd <= 0:
                        # 测速失败（瞬时抖动/超时）：重试一次，提高成功率避免误杀
                        if engine == "native":
                            spd = await measure_speed_native(candidate.node, timeout, speed_bytes,
                                                             proxy, speed_url, speed_port)
                        else:
                            spd = await asyncio.to_thread(
                                measure_speed_with_curl, candidate.node, timeout, process_buffer,
                                speed_bytes, proxy, speed_url, speed_port,
                            )
                    if spd > 0:
                        speeds.append(spd)
                # 多次采样取中位数，抗瞬时抖动
                speed = float(statistics.median(speeds)) if speeds else 0.0
                result = SpeedResult(
                    node=candidate.node,
                    latency_ms=candidate.latency_ms,
                    speed_mbps=speed,
                    is_fast=speed > min_speed,
                    packet_loss=candidate.packet_loss,
                )
                results.append(result)
                if result.is_fast:
                    fast_count += 1
                    if stop_after_fast > 0 and fast_count >= stop_after_fast:
                        stop_requested = True
                if verbose:
                    status = "FAST" if result.is_fast else "NORMAL"
                    tqdm.write(f"[SPEED] {candidate.node.raw} -> {speed} Mbps {status}")
                done_count += 1
                if done_count % step == 0 or done_count >= len(candidates):
                    emit_progress(json_progress, progress, len(candidates),
                                  "下载速度测试...", done=done_count)
                progress.update(1)
            except Exception as exc:  # noqa: BLE001  # 单点失败不能挂死整个队列
                error_count += 1
                tqdm.write(f"[SPEED] worker error on {candidate.node.raw}: {exc}")
                # 即使单节点测速异常，也要产生一个明确的失败结果并推进
                # 进度；不能吞掉任务后让 CLI 看起来像提前完成。
                results.append(SpeedResult(
                    node=candidate.node,
                    latency_ms=candidate.latency_ms,
                    speed_mbps=0.0,
                    is_fast=False,
                    packet_loss=candidate.packet_loss,
                ))
                done_count += 1
            finally:
                queue.task_done()

    task_count = positive_worker_count(workers, len(candidates))
    tasks = [asyncio.create_task(worker()) for _ in range(task_count)]
    for candidate in candidates:
        queue.put_nowait(candidate)
    for _ in tasks:
        queue.put_nowait(None)
    await queue.join()
    await asyncio.gather(*tasks)
    emit_progress(json_progress, progress, len(candidates), "下载速度测试...", done=done_count)
    progress.close()
    if stop_after_fast > 0 and fast_count >= stop_after_fast:
        print(f"Early stop: reached {fast_count} fast nodes; tested {done_count}/{len(candidates)} candidates")
    elif len(results) != len(candidates):
        print(f"WARNING: speed stage produced {len(results)}/{len(candidates)} results")
    if error_count:
        print(f"Speed test errors: {error_count}; failed nodes retained with 0.0 Mbps")
    return results


# ---------------------------------------------------------------------------
# 候选选择与结果
# ---------------------------------------------------------------------------
def _eligible_candidates(results: Sequence[TcpResult], max_packet_loss: float) -> list[TcpResult]:
    """按丢包阈值预筛候选；未开启阈值时完整保留旧行为。"""
    threshold = min(100.0, max(0.0, float(max_packet_loss)))
    if threshold >= 100.0:
        return list(results)
    return [result for result in results if result.packet_loss <= threshold]


def _candidate_sort_key(result: TcpResult) -> tuple[float, float]:
    """延迟优先；延迟相同或接近时优先低丢包节点。"""
    return result.latency_ms, result.packet_loss


def _select_regional_candidates(results: Sequence[TcpResult], top_per_region: int) -> list[TcpResult]:
    """每个区域保留延迟最低的 top_per_region 个候选。"""
    groups: dict[str, list] = defaultdict(list)
    limit = max(1, top_per_region)
    for result in results:
        groups[result.node.region].append(result)
    candidates = [
        result
        for region in groups
        for result in sorted(groups[region], key=_candidate_sort_key)[:limit]
    ]
    candidates.sort(key=lambda item: (item.node.region, *_candidate_sort_key(item)))
    return candidates


def select_candidates(results: Sequence[TcpResult], top_per_region: int,
                      mode: str = DEFAULT_CANDIDATE_MODE,
                      global_limit: int = DEFAULT_CANDIDATE_LIMIT,
                      max_packet_loss: float = DEFAULT_MAX_PACKET_LOSS) -> list[TcpResult]:
    """按策略选择下载测速候选，并可排除超过丢包阈值的复测结果。

    regional：每区前 N；global：全局延迟前 K；adaptive：每区前 3 + 全局前 K，去重。
    默认阈值为 100%，因此不会改变既有 CLI 行为。
    """
    eligible = _eligible_candidates(results, max_packet_loss)
    if mode == "global":
        return sorted(eligible, key=_candidate_sort_key)[:max(1, global_limit)]
    if mode == "adaptive":
        # 自适应模式保留用户设置的每区数量；此前硬编码最多 3 个会让
        # 1 万级节点库在下载阶段只剩很小一撮，界面设置也失去意义。
        regional = _select_regional_candidates(eligible, max(1, top_per_region))
        global_top = sorted(eligible, key=_candidate_sort_key)[:max(1, global_limit)]
        merged: list[TcpResult] = []
        seen: set[Node] = set()
        for result in regional + global_top:
            if result.node in seen:
                continue
            seen.add(result.node)
            merged.append(result)
        merged.sort(key=lambda item: (item.node.region, *_candidate_sort_key(item)))
        return merged
    if mode != "regional":
        print(f"WARNING: unknown candidate mode {mode!r}, falling back to 'regional'")
    return _select_regional_candidates(eligible, top_per_region)


def filter_fast_results(results: Sequence[SpeedResult]) -> list[SpeedResult]:
    return [r for r in results if r.is_fast]
















# ---------------------------------------------------------------------------
async def benchmark_network(config: AppConfig) -> tuple[float, float] | None:
    """测基准节点：返回 (latency_ms, speed_mbps)；失败返回 None。"""
    node = parse_benchmark_node(config.benchmark)
    if node is None:
        print(f"WARNING: invalid benchmark {config.benchmark!r}; skipping")
        return None
    latency = await tcping(node, config.tcp_timeout, samples=3)
    if latency is None:
        print(f"WARNING: benchmark {config.benchmark} unreachable; skipping")
        return None  # tcping 失败，不浪费测速时间
    speed = 0.0
    speed_proxy = config.speed_proxy or config.proxy
    try:
        if config.speed_engine == "native":
            speed = await measure_speed_native(node, config.speed_timeout,
                                               BENCHMARK_SPEED_BYTES, speed_proxy,
                                               config.speed_url, config.speed_port)
        else:
            speed = await asyncio.to_thread(
                measure_speed_with_curl, node, config.speed_timeout,
                config.speed_process_buffer, BENCHMARK_SPEED_BYTES,
                speed_proxy, config.speed_url, config.speed_port)
    except Exception:
        speed = 0.0
    return latency, speed


async def _reverify_speed(node: Node, config: AppConfig, timeout: float) -> float:
    """对单个节点执行一次验证测速；失败/异常返回 0.0。"""
    speed_proxy = config.speed_proxy or config.proxy
    try:
        if config.speed_engine == "native":
            return await measure_speed_native(node, timeout, BENCHMARK_SPEED_BYTES,
                                              speed_proxy, config.speed_url,
                                              config.speed_port)
        return await asyncio.to_thread(
            measure_speed_with_curl, node, timeout, config.speed_process_buffer,
            BENCHMARK_SPEED_BYTES, speed_proxy, config.speed_url, config.speed_port)
    except Exception:
        return 0.0


async def verify_best_results(results: list[SpeedResult], config: AppConfig) -> list[SpeedResult]:
    """对优选结果二次验证：重新测速，失效或明显变慢的移除。

    单次重测返回 0（网络抖动/瞬时失败）时重试一次再判定，避免误杀好 IP。
    """
    if config.verify_top <= 0 or not results:
        return results
    keep: list[SpeedResult] = []
    verify_timeout = max(2.0, config.speed_timeout * 0.6)
    for r in results[:config.verify_top]:
        speed = await _reverify_speed(r.node, config, verify_timeout)
        if speed <= 0:
            # 瞬时抖动重试一次
            speed = await _reverify_speed(r.node, config, verify_timeout)
        if speed <= 0:
            # 验证重测失败（网络抖动/瞬时不可达）：保留原结果，避免误杀好 IP
            keep.append(r)
        elif speed >= r.speed_mbps * VERIFY_MIN_RATIO:
            keep.append(SpeedResult(node=r.node, latency_ms=r.latency_ms,
                                    speed_mbps=speed,
                                    is_fast=speed > config.min_speed_mbps,
                                    packet_loss=r.packet_loss))
        else:
            print(f"[VERIFY] dropped {r.node.raw}: re-test {speed:.2f}Mbps vs {r.speed_mbps:.2f}Mbps")
    keep.extend(results[config.verify_top:])
    return keep


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
async def run(config: AppConfig) -> int:
    if config.full_output_file.resolve() == config.best_output_file.resolve():
        print("ERROR: --output and --best-output must point to different files")
        return 1

    # 基准测试：衡量当前网络水平
    baseline: tuple[float, float] | None = None
    if config.benchmark_enabled:
        baseline = await benchmark_network(config)
        if baseline is not None:
            lat, spd = baseline
            lat_txt = f"{lat}ms" if lat is not None else "--"
            spd_txt = f"{spd:.2f}Mbps" if spd and spd > 0 else "--"
            print(f"Baseline: {config.benchmark} -> {lat_txt} | {spd_txt} "
                  f"(current network reference)")

    if config.input_mode == "official":
        # 官方 IP 段数据源：下载 Cloudflare 官方段，每段随机采样候选 IP（借鉴 CFData-WEB 官方优选）
        nodes = generate_official_nodes(
            iptype=config.official_iptype,
            per_prefix=config.official_per_prefix,
            timeout=DEFAULT_INPUT_DOWNLOAD_TIMEOUT,
        )
        if not nodes:
            print("ERROR: failed to generate official IP nodes")
            return 1
        # 写回 input_file，便于复用与 TCP 缓存
        config.input_file.parent.mkdir(parents=True, exist_ok=True)
        with config.input_file.open("w", encoding="utf-8", newline="\n") as fh:
            for n in nodes:
                fh.write(n.raw + "\n")
        print(f"Generated {len(nodes)} official IP nodes -> {config.input_file}")

    elif config.input_urls:
        # 多数据源：从所有 URL 下载合并去重，写到 input_file
        if config.input_explicit:
            print(f"WARNING: --input-url will overwrite local file {config.input_file} "
                  f"(remove -i to keep it)")
        input_proxy = config.input_proxy or config.proxy
        nodes = merge_nodes_from_urls(config.input_urls, config.input_file,
                                      DEFAULT_INPUT_DOWNLOAD_TIMEOUT,
                                      proxy=input_proxy)
        if not nodes:
            print("ERROR: no valid nodes from any input URL")
            return 1
        print(f"Loaded {len(nodes)} nodes from {len(config.input_urls)} URL(s) into {config.input_file}")
    elif config.input_explicit:
        # 显式 -i：尊重本地文件，绝不联网下载（文件缺失/为空直接报错，不卡在下载）
        try:
            nodes = load_nodes(config.input_file)
        except FileNotFoundError as exc:
            print(f"ERROR: {exc}")
            return 1
        if not nodes:
            print(f"ERROR: no valid nodes found in {config.input_file}")
            return 1
    else:
        url_file = Path("_ips_url.txt")
        raw_url = url_file.read_text(encoding="utf-8").strip() if url_file.exists() else ""
        # 多源 fallback：用户源优先，失败依次尝试内置备选源
        sources = [raw_url] if raw_url else []
        for u in DEFAULT_INPUT_URLS:
            if u not in sources:
                sources.append(u)
        nodes = []
        for src in sources:
            if not src:
                continue
            input_proxy = config.input_proxy or config.proxy
            ok = refresh_input_file_with_fallback(
                src, config.input_file, DEFAULT_INPUT_DOWNLOAD_TIMEOUT,
                proxy=input_proxy)
            if ok:
                try:
                    nodes = load_nodes(config.input_file)
                except FileNotFoundError:
                    nodes = []
                if nodes:
                    print(f"Loaded {len(nodes)} nodes from {src}")
                    break
        if not nodes:
            # 全部源失败：回退本地文件（历史 ips.txt）
            try:
                nodes = load_nodes(config.input_file)
                if nodes:
                    print(f"WARNING: all sources failed; using local {config.input_file}")
            except FileNotFoundError:
                nodes = []
        if not nodes:
            print("ERROR: no valid nodes found. Check network/proxy or change source.")
            print("       tried: " + ", ".join(s for s in sources if s))
            print("       tip: 数据源 '官方IPv4段'/'官方IPv6段' 不依赖第三方列表，可绕过此问题")
            return 1
    # --ports: 为每个 IP 扩展出额外端口（去重，保留原端口）
    if config.ports:
        extra = set(config.ports)
        expanded: list[Node] = []
        seen: set[Node] = set()
        for n in nodes:
            for port in sorted({n.port} | extra):
                cand = Node(n.ip, port, n.region)
                if cand not in seen:
                    seen.add(cand)
                    expanded.append(cand)
        nodes = expanded
    print(f"Loaded {len(nodes)} unique nodes from {config.input_file}")

    # TCP 结果缓存（增量）：复用上次可达 IP 的延迟，只测新 IP/失败 IP
    cache_path = config.input_file.parent / DEFAULT_CACHE_FILE
    cache: dict[str, TcpResult] = {}
    # 官方段每次生成随机 IP：缓存永不命中且文件无限膨胀，跳过缓存读写
    if config.input_mode == "official":
        cache = {}
    elif not config.no_cache:
        cache = load_tcp_cache(cache_path)

    cached: list[TcpResult] = []
    to_test: list[Node] = []
    for n in nodes:
        hit = cache.get(n.raw)
        if hit is not None:
            cached.append(hit)
        else:
            to_test.append(n)

    _run_start = time.perf_counter()
    print(f"Stage 1/2: TCP latency test, concurrency={config.tcp_workers}"
          f" (cached: {len(cached)}, to-test: {len(to_test)})")
    _tcp_start = time.perf_counter()
    fresh: list[TcpResult] = []
    try:
        if to_test:
            fresh = await run_tcp_tests(to_test, config.tcp_timeout, config.tcp_workers,
                                        config.verbose, config.json_progress, config.tcp_samples,
                                        mode=config.scan_mode)
        tcp_results = fresh + cached
        # 全量初测成功率异常低时，优先怀疑 timeout/并发造成的瞬时误判，
        # 而不是直接把少量初测成功结果交给候选筛选。补测只使用本轮数据。
        if (to_test and len(to_test) >= 100
                and len(fresh) / len(to_test) < 0.25):
            recovered = await recover_failed_tcp_tests(
                to_test, fresh, config.tcp_timeout, config.tcp_workers,
                config.verbose, config.json_progress, config.scan_mode,
            )
            fresh.extend(recovered)
            tcp_results = fresh + cached
        if config.recheck_top > 0 and config.scan_mode == "tcping":
            tcp_results = await recheck_latency_results(
                tcp_results, config.tcp_timeout, config.recheck_top, config.recheck_samples)
    finally:
        # 取消/异常时也保存已完成的 TCP 结果，避免下次全量重测（官方段模式除外）
        if not config.no_cache and config.input_mode != "official":
            try:
                save_tcp_cache(cache_path, fresh + cached)
            except OSError:
                pass
    # 地区标注：对 region 为空的节点查 IP 归属（不自动筛选，只标注供用户自己选）
    if config.annotate:
        cache_path = config.input_file.parent / ANNOTATE_CACHE_FILE
        tcp_results = annotate_regions(list(tcp_results), cache_path)
    candidates = select_candidates(tcp_results, config.top_per_region,
                                   config.candidate_mode, config.candidate_limit,
                                   config.max_packet_loss)
    if config.max_packet_loss < 100.0:
        eligible_count = len(_eligible_candidates(tcp_results, config.max_packet_loss))
        print(f"Packet-loss filter: <= {config.max_packet_loss:.1f}%; "
              f"eligible {eligible_count}/{len(tcp_results)}, candidates {len(candidates)}")
    tcp_elapsed = time.perf_counter() - _tcp_start
    print(f"Stage 1/2 complete: TCP reachable {len(tcp_results)}/{len(nodes)}; "
          f"recheck top {min(config.recheck_top, len(tcp_results)) if config.recheck_top > 0 else 0}; "
          f"candidates {len(candidates)}; elapsed {tcp_elapsed:.1f}s")
    total_nodes = max(1, len(nodes))
    reach_rate = len(tcp_results) / total_nodes * 100.0
    print(f"TCP reachable: {len(tcp_results)}/{len(nodes)} ({reach_rate:.1f}%); "
          f"speed candidates: {len(candidates)}")
    if reach_rate < 5.0:
        print("WARNING: TCP reachability very low (<5%). Likely tcp-timeout too short "
              "or tcp-workers too high for cross-border links.")
        print(f"        current: tcp-timeout={config.tcp_timeout}s, "
              f"tcp-workers={config.tcp_workers}; try timeout>=1.0s, workers<=200")

    speed_results: list[SpeedResult] = []
    if candidates:
        _speed_start = time.perf_counter()
        speed_url = config.speed_url
        if not speed_url:
            # 自动测速源：默认源不可达时按序切换，避免单源抖动误杀（借鉴 CFData-WEB auto）
            speed_url = _auto_detect_speed_url()
            if speed_url:
                print(f"Auto-selected speed source: {speed_url}")
            else:
                print("WARNING: no auto speed source reachable; using built-in default")
        print(f"Stage 2/2 starting: download speed test, candidates={len(candidates)}, "
              f"concurrency={config.speed_workers}, fast tag > {config.min_speed_mbps} Mbps, "
              f"source={_display_speed_url(speed_url)}")
        speed_kwargs = {}
        if config.stop_after_fast > 0:
            speed_kwargs["stop_after_fast"] = config.stop_after_fast
        # speed_proxy 为空表示节点测速直连；config.proxy 仅为旧 CLI 保持
        # “输入和测速共用代理”的兼容行为，GUI 使用新字段后不会走这里。
        speed_proxy = config.speed_proxy or config.proxy
        speed_results = await run_speed_tests(
            candidates, config.speed_timeout, config.speed_process_buffer,
            config.speed_workers, config.min_speed_mbps, config.verbose,
            config.json_progress, config.speed_bytes, config.speed_engine,
            speed_proxy, speed_url, config.speed_port,
            config.speed_samples, **speed_kwargs,
        )

    best_results = filter_fast_results(speed_results)
    # 优选结果二次验证（失效/变慢移除）
    if best_results and config.verify_top > 0:
        best_results = await verify_best_results(best_results, config)
    write_results(config.full_output_file, speed_results, sort=config.sort, regions=config.regions)
    write_results(config.best_output_file, best_results, sort=config.sort, regions=config.regions)
    # 阶段耗时统计
    if "_tcp_start" in locals() and "_speed_start" in locals() and "_run_start" in locals():
        timings = {
            "tcp": (locals()["_speed_start"] - locals()["_tcp_start"]),
            "speed": (time.perf_counter() - locals()["_speed_start"]),
            "total": (time.perf_counter() - locals()["_run_start"]),
        }
    else:
        timings = None
    # 统一去重：--csv 与 --export csv 效果合并，CSV 只导出一次
    formats = set(config.export_formats)
    if config.csv_export:
        formats.add("csv")
    for fmt in sorted(formats):
        if fmt == "csv":
            export_csv(config.full_output_file.with_suffix(".csv"), speed_results,
                       sort=config.sort, regions=config.regions)
            export_csv(config.best_output_file.with_suffix(".csv"), best_results,
                       sort=config.sort, regions=config.regions)
        elif fmt == "v2ray":
            export_v2ray(config.full_output_file.with_suffix(".v2ray.txt"), speed_results,
                         sort=config.sort, regions=config.regions)
            export_v2ray(config.best_output_file.with_suffix(".v2ray.txt"), best_results,
                         sort=config.sort, regions=config.regions)
        elif fmt == "clash":
            export_clash(config.full_output_file.with_suffix(".clash.yaml"), speed_results,
                         sort=config.sort, regions=config.regions)
            export_clash(config.best_output_file.with_suffix(".clash.yaml"), best_results,
                         sort=config.sort, regions=config.regions)
        elif fmt == "singbox":
            export_singbox(config.full_output_file.with_suffix(".singbox.json"), speed_results,
                           sort=config.sort, regions=config.regions)
            export_singbox(config.best_output_file.with_suffix(".singbox.json"), best_results,
                           sort=config.sort, regions=config.regions)
    print_summary(config, len(nodes), len(tcp_results), len(speed_results), len(best_results),
                 timings if "timings" in locals() else None,
                 baseline)

    return 0



def print_summary(config: AppConfig, input_count: int, tcp_count: int,
                  speed_count: int, fast_count: int,
                  timings: dict[str, float] | None = None,
                  baseline: tuple[float, float] | None = None) -> None:
    print("Done")
    print(f"Input nodes: {input_count}")
    print(f"TCP reachable: {tcp_count}")
    print(f"Speed tested: {speed_count}")
    print(f"Fast tagged: {fast_count}")
    if timings:
        parts = ", ".join(f"{k}: {v:.1f}s" for k, v in timings.items())
        print(f"Elapsed: {parts}")
    if baseline is not None and (baseline[0] is not None or baseline[1] > 0):
        lat_txt = f"{baseline[0]}ms" if baseline[0] is not None else "--"
        spd_txt = f"{baseline[1]:.2f}Mbps" if baseline[1] > 0 else "--"
        print(f"Baseline: {config.benchmark} -> {lat_txt} | {spd_txt}")
    print(f"Full output: {config.full_output_file}")
    print(f"Best output: {config.best_output_file}")


async def run_scheduled(config: AppConfig) -> int:
    """按 interval_seconds 循环运行 run()；任一循环失败不中断后续。"""
    import itertools
    last_rc = 0
    for round_no in itertools.count(1):
        print(f"\n=== Scheduled round #{round_no} @ {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
        try:
            last_rc = await run(config)
        except Exception as exc:  # noqa: BLE001  # 单轮失败继续下一轮
            print(f"Scheduled round #{round_no} failed: {exc}")
            last_rc = 1
        print(f"=== Round #{round_no} finished (rc={last_rc}); "
              f"next in {config.interval_seconds}s (Ctrl+C to stop) ===")
        try:
            await asyncio.sleep(config.interval_seconds)
        except asyncio.CancelledError:
            print("\nScheduled loop cancelled.")
            return 130
    return last_rc


def _cleanup_stale_mei_dirs(max_age_hours: float = 6.0) -> int:
    r"""清理 PyInstaller onefile 残留的解压目录（%TEMP%\_MEI*）。

    被强杀（如停止按钮 taskkill /F）时不清理会累积数十 GB；本函数删除过期残留。
    """
    if not getattr(sys, "frozen", False):
        return 0
    try:
        current = getattr(sys, "_MEIPASS", None)
        tmp = Path(_tempfile.gettempdir())
        cutoff = time.time() - max_age_hours * 3600
        removed = 0
        for d in tmp.glob("_MEI*"):
            if not d.is_dir():
                continue
            if current and str(d.resolve()) == str(Path(current).resolve()):
                continue  # 当前进程在用
            try:
                if d.stat().st_mtime > cutoff:
                    continue  # 太新，可能别的实例正在用
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
            except OSError:
                continue
        if removed:
            print(f"Cleaned {removed} stale PyInstaller temp dir(s)")
        return removed
    except Exception:
        return 0


def main() -> int:
    """入口；--interval > 0 时按指定秒数循环重跑（Ctrl+C 停止）。"""
    # 确保 stdout 行缓冲 + UTF-8（中文 Windows 默认 GBK，GUI 按 UTF-8 读会乱码）
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
        except Exception:
            pass
    _cleanup_stale_mei_dirs()
    try:
        config = parse_args()
        if getattr(config, "interval_seconds", 0) <= 0:
            return asyncio.run(run(config))
        return asyncio.run(run_scheduled(config))
    except KeyboardInterrupt:
        print("\nInterrupted by user (Ctrl+C). Partial results may be saved.")
        return 130
    except asyncio.CancelledError:
        print("\nTask cancelled.")
        return 130


if __name__ == "__main__":
    sys.exit(main())

