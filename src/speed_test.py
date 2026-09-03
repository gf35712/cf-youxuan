# -*- coding: utf-8 -*-
"""下载测速实现：curl 与原生 asyncio TLS 引擎。"""
from __future__ import annotations

import asyncio
import socket
import ssl
import subprocess
import sys
import time

from models import Node

SPEED_DOMAIN = "speed.cloudflare.com"
SPEED_PATH = "/__down"
DEFAULT_SPEED_BYTES = 4 * 1024 * 1024
BENCHMARK_SPEED_BYTES = 1 * 1024 * 1024


def parse_curl_speed(stdout: str) -> float:
    """解析 curl -w 输出，返回 Mbps；仅接受 2xx HTTP 响应。"""
    try:
        parts = stdout.strip().split()
        size_bytes = float(parts[0])
        time_total = float(parts[1])
        time_start = float(parts[2]) if len(parts) > 2 else 0.0
    except (ValueError, IndexError):
        return 0.0
    if len(parts) >= 4:
        try:
            status_code = int(float(parts[3]))
        except ValueError:
            return 0.0
        if not 200 <= status_code < 300:
            return 0.0
    if size_bytes <= 0 or time_total <= 0:
        return 0.0
    download_time = time_total - time_start if 0 < time_start < time_total else time_total
    if download_time <= 0:
        return 0.0
    return round(size_bytes * 8 / (download_time * 1_000_000), 2)


def measure_speed_with_curl(node: Node, timeout: float, process_buffer: float,
                            speed_bytes: int = DEFAULT_SPEED_BYTES,
                            proxy: str = "", speed_url: str = "",
                            speed_port: int = 0) -> float:
    """经 curl 下载测速，返回 Mbps；失败返回 0。"""
    from update import _parse_speed_url, get_curl_command
    curl = get_curl_command()
    if curl is None:
        return 0.0
    domain, path, url_port = _parse_speed_url(speed_url)
    port = speed_port or url_port or node.port
    url = f"https://{domain}:{port}{path}?bytes={speed_bytes}"
    null_dev = "NUL" if sys.platform == "win32" else "/dev/null"
    resolve_ip = f"[{node.ip}]" if ":" in node.ip else node.ip
    cmd = [
        curl, "-s", "-o", null_dev,
        "-w", "%{size_download} %{time_total} %{time_starttransfer} %{http_code}",
        "--resolve", f"{domain}:{port}:{resolve_ip}",
        "--connect-timeout", str(min(5, timeout)),
        "--max-time", str(timeout),
        "--insecure",
    ]
    if proxy:
        cmd += ["--proxy", proxy]
    cmd.append(url)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + process_buffer,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0.0
    return parse_curl_speed(result.stdout)


async def _native_download(reader, writer, timeout: float, speed_bytes: int,
                           speed_url: str = "", speed_port: int = 0) -> float:
    """向已建立的连接发送 GET 并统计下载速率。"""
    from update import _parse_speed_url
    domain, path, url_port = _parse_speed_url(speed_url)
    port = speed_port or url_port or 443
    request = (
        f"GET {path}?bytes={speed_bytes} HTTP/1.1\r\n"
        f"Host: {domain}:{port}\r\n"
        "User-Agent: cf-ip-updater/1.0\r\n"
        "Connection: close\r\n\r\n"
    )
    try:
        start = None
        writer.write(request.encode("ascii"))
        await writer.drain()
        received = 0
        header_done = False
        header_buf = b""
        while received < speed_bytes:
            try:
                chunk = await asyncio.wait_for(reader.read(65536), timeout=timeout)
            except (asyncio.TimeoutError, TimeoutError):
                break
            if not chunk:
                break
            if not header_done:
                header_buf += chunk
                idx = header_buf.find(b"\r\n\r\n")
                if idx != -1:
                    header_done = True
                    start = time.perf_counter()
                    received = len(header_buf) - (idx + 4)
            else:
                if start is None:
                    start = time.perf_counter()
                received += len(chunk)
        elapsed = (time.perf_counter() - start) if start is not None else 0.0
        if received <= 0 or elapsed <= 0:
            return 0.0
        return round(received * 8 / (elapsed * 1_000_000), 2)
    except (OSError, ssl.SSLError, ConnectionError):
        return 0.0
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except (OSError, ssl.SSLError, ConnectionError):
            pass


async def measure_speed_native(node: Node, timeout: float, speed_bytes: int,
                               proxy: str = "", speed_url: str = "",
                               speed_port: int = 0) -> float:
    """原生 asyncio TLS 测速：直连 node.ip:port，SNI=测速域名。"""
    from update import _parse_speed_url
    domain, _path, url_port = _parse_speed_url(speed_url)
    port = speed_port or url_port or node.port
    family = socket.AF_INET6 if ":" in node.ip else socket.AF_INET
    if proxy:
        return await _measure_speed_native_proxy(node, timeout, speed_bytes, proxy,
                                                 speed_url, speed_port)
    try:
        context = ssl.create_default_context()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(node.ip, port, ssl=context,
                                    server_hostname=domain, family=family),
            timeout=min(5.0, timeout),
        )
    except (OSError, ssl.SSLError, asyncio.TimeoutError, TimeoutError):
        return 0.0
    return await _native_download(reader, writer, timeout, speed_bytes, speed_url, port)


async def _measure_speed_native_proxy(node: Node, timeout: float, speed_bytes: int,
                                      proxy: str, speed_url: str = "",
                                      speed_port: int = 0) -> float:
    """经 HTTP CONNECT 代理测速（原生引擎 + --proxy）。"""
    from urllib.parse import urlsplit
    from update import _parse_speed_url
    domain, _path, url_port = _parse_speed_url(speed_url)
    port = speed_port or url_port or node.port
    parsed = urlsplit(proxy if "://" in proxy else "http://" + proxy)
    if parsed.scheme not in ("http",):
        return 0.0
    proxy_host = parsed.hostname
    proxy_port = parsed.port or 80
    if not proxy_host:
        return 0.0
    target = f"{domain}:{port}"
    writer = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(proxy_host, proxy_port),
            timeout=min(5.0, timeout),
        )
        connect_req = f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n"
        writer.write(connect_req.encode("ascii"))
        await writer.drain()
        response = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=timeout)
        if not response.startswith(b"HTTP/1.1 200"):
            return 0.0
        # 对已有代理 socket 升级 TLS，保持原有行为。
        context = ssl.create_default_context()
        tls_reader, tls_writer = await asyncio.wait_for(
            asyncio.open_connection(
                host=domain, port=port, ssl=context,
                server_hostname=domain,
                sock=writer.transport.get_extra_info("socket"),
            ),
            timeout=timeout,
        )
        writer = None  # TLS writer 接管 socket 生命周期
        return await _native_download(tls_reader, tls_writer, timeout, speed_bytes, speed_url, port)
    except (OSError, ssl.SSLError, asyncio.TimeoutError, TimeoutError,
            asyncio.IncompleteReadError, ConnectionError):
        return 0.0
    except Exception:
        return 0.0
    finally:
        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except (OSError, ssl.SSLError, ConnectionError):
                pass
