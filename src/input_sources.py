# -*- coding: utf-8 -*-
"""CF 优选输入源边界：在线列表、官方 IP 段与本地输入下载。"""
from __future__ import annotations

import ipaddress
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path

from models import Node

DEFAULT_INPUT_FILE = Path("ips.txt")
DEFAULT_INPUT_URL = "https://zip.cm.edu.kg/all.txt"
DEFAULT_INPUT_URLS = (
    "https://zip.cm.edu.kg/all.txt",
    "https://zip.baipiao.eu.org/all.txt",
    "https://zip.baipiao.eu.org/part/001.txt",
)
DEFAULT_INPUT_DOWNLOAD_TIMEOUT = 10.0
MAX_INPUT_DOWNLOAD_BYTES = 20 * 1024 * 1024
OFFICIAL_PREFIXES_V4_URL = "https://www.cloudflare.com/ips-v4"
OFFICIAL_PREFIXES_V6_URL = "https://www.cloudflare.com/ips-v6"
_FALLBACK_CF_IPV4_PREFIXES = (
    "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "104.16.0.0/13",
    "104.24.0.0/14", "108.162.192.0/18", "131.0.72.0/22", "141.101.64.0/18",
    "162.158.0.0/15", "172.64.0.0/13", "173.245.48.0/20", "188.114.96.0/20",
    "190.93.240.0/20", "197.234.240.0/22", "198.41.128.0/17",
)
_FALLBACK_CF_IPV6_PREFIXES = (
    "2400:cb00::/32", "2405:b500::/32", "2405:8100::/32", "2606:4700::/32",
    "2803:f800::/32", "2a06:98c0::/29", "2c0f:f248::/32",
)


def refresh_input_file(url: str, path: Path, timeout: float, *, skip_existing: bool = False,
                       proxy: str = "") -> bool:
    """从 URL 下载 IP 列表到 path，并在替换前校验至少一个有效节点。"""
    if skip_existing and path.exists() and path.stat().st_size > 0:
        return False
    if not url.strip():
        print("Input download failed: empty URL; using local " + str(path))
        return False
    if not url.lower().startswith(("http://", "https://")):
        print(f"Input download failed: URL scheme not allowed ({url[:50]}...); using local {path}")
        return False
    temp_path = path.with_name(path.name + ".download")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "cf-ip-updater/1.0"})
        try:
            if proxy:
                handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            else:
                # 直连回退必须显式禁用系统环境代理；否则 HTTP_PROXY 等
                # 环境变量可能仍把“测速直连”绕回已关闭的本地代理。
                handler = urllib.request.ProxyHandler({})
            opener = urllib.request.build_opener(handler)
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            try:
                exc.close()
            except Exception:
                pass
            raise
        with response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
            with temp_path.open("wb") as file:
                total = 0
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_INPUT_DOWNLOAD_BYTES:
                        raise RuntimeError(f"downloaded file exceeds {MAX_INPUT_DOWNLOAD_BYTES} bytes")
                    file.write(chunk)
        if temp_path.stat().st_size == 0:
            raise RuntimeError("downloaded file is empty")
        from update import parse_node
        with temp_path.open("r", encoding="utf-8-sig", errors="replace") as file:
            valid = any(parse_node(line) is not None for line in file)
        if not valid:
            raise RuntimeError("downloaded file contains no valid nodes")
        temp_path.replace(path)
        print(f"Downloaded input file from {url} to {path}")
        return True
    except (OSError, RuntimeError, ValueError, urllib.error.URLError) as exc:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        print(f"Input download failed: {exc}; retrying with curl ...")
        return _download_with_curl(url, temp_path, path, timeout, proxy=proxy)


def refresh_input_file_with_fallback(url: str, path: Path, timeout: float, *,
                                     skip_existing: bool = False,
                                     proxy: str = "") -> bool:
    """下载输入列表；代理失败后自动直连，不让失效本地代理卡死流程。"""
    if proxy:
        if refresh_input_file(url, path, timeout, skip_existing=skip_existing, proxy=proxy):
            return True
        print("WARNING: input proxy unavailable; retrying input list directly ...")
        return refresh_input_file(url, path, timeout, skip_existing=False, proxy="")
    return refresh_input_file(url, path, timeout, skip_existing=skip_existing, proxy="")


def _download_with_curl(url: str, temp_path: Path, dest: Path, timeout: float,
                        proxy: str = "") -> bool:
    from update import get_curl_command
    curl = get_curl_command()
    if curl is None:
        print(f"curl not available; using local {dest}")
        return False
    try:
        cmd = [curl, "-sSL", "-f", "--connect-timeout", "5", "--max-time", str(timeout),
               "--max-filesize", str(MAX_INPUT_DOWNLOAD_BYTES), "-o", str(temp_path)]
        if proxy:
            cmd += ["--proxy", proxy]
        else:
            cmd += ["--noproxy", "*"]
        cmd.append(url)
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + 10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"curl download error: {exc}; using local {dest}")
        return False
    if result.returncode != 0 or not temp_path.exists() or temp_path.stat().st_size == 0:
        print(f"curl download failed (exit={result.returncode}); using local {dest}")
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        return False
    temp_path.replace(dest)
    print(f"Downloaded input file from {url} to {dest}")
    return True


def merge_nodes_from_urls(urls: Sequence[str], path: Path,
                          timeout: float | None = None, proxy: str = "",
                          download_fn: Callable | None = None) -> list[Node]:
    """从多个 URL 下载 IP 列表，合并去重，返回 Node 列表。"""
    import hashlib
    if timeout is None:
        timeout = DEFAULT_INPUT_DOWNLOAD_TIMEOUT
    download_fn = download_fn or refresh_input_file_with_fallback
    all_nodes: list[Node] = []
    seen: set[Node] = set()
    from update import parse_node
    for url in urls:
        digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
        temp_path = path.with_name(f"{path.name}.{digest}.download")
        try:
            ok = download_fn(url, temp_path, timeout, skip_existing=False, proxy=proxy)
            if ok and temp_path.exists():
                with temp_path.open("r", encoding="utf-8-sig") as file:
                    for line in file:
                        node = parse_node(line)
                        if node is not None and node not in seen:
                            seen.add(node)
                            all_nodes.append(node)
            else:
                print(f"WARNING: failed to load from {url}, skipping")
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            print(f"WARNING: failed to load from {url}: {exc}")
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
    if all_nodes:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as file:
            for node in all_nodes:
                file.write(node.raw + "\n")
    return all_nodes


def _fetch_official_prefixes(iptype: int, timeout: float) -> tuple[str, ...]:
    """下载 Cloudflare 官方 IP 段；失败时回退内置列表。"""
    url = OFFICIAL_PREFIXES_V4_URL if iptype == 4 else OFFICIAL_PREFIXES_V6_URL
    fallback = _FALLBACK_CF_IPV4_PREFIXES if iptype == 4 else _FALLBACK_CF_IPV6_PREFIXES
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "cf-ip-updater/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read(64 * 1024).decode("utf-8", errors="replace")
        valid = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ipaddress.ip_network(line, strict=False)
                valid.append(line)
            except ValueError:
                continue
        if valid:
            return tuple(valid)
    except urllib.error.HTTPError as exc:
        try:
            exc.close()
        except Exception:
            pass
    except (OSError, ValueError, urllib.error.URLError):
        pass
    print(f"WARNING: failed to fetch official IPv{iptype} prefixes; using built-in fallback")
    return fallback


def generate_official_nodes(iptype: int = 4, per_prefix: int = 5, port: int = 443,
                            timeout: float | None = None, fetch_fn: Callable | None = None) -> list[Node]:
    """按 Cloudflare 官方 IP 段每段随机采样候选节点。"""
    import random
    if timeout is None:
        timeout = DEFAULT_INPUT_DOWNLOAD_TIMEOUT
    fetch_fn = fetch_fn or _fetch_official_prefixes
    rng = random.SystemRandom()
    per_prefix = max(1, min(int(per_prefix), 32))
    nodes: list[Node] = []
    seen: set[Node] = set()
    for prefix_txt in fetch_fn(iptype, timeout):
        try:
            network = ipaddress.ip_network(prefix_txt, strict=False)
        except ValueError:
            continue
        total = network.num_addresses
        if total <= 2:
            continue
        chosen: set = set()
        tried = 0
        while len(chosen) < per_prefix and tried < per_prefix * 8:
            tried += 1
            idx = rng.randrange(1, total - 1)
            addr = network.network_address + idx
            if addr in chosen:
                continue
            chosen.add(addr)
            node = Node(str(addr), port, region="")
            if node not in seen:
                seen.add(node)
                nodes.append(node)
    return nodes
