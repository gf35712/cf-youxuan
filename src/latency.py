# -*- coding: utf-8 -*-
"""TCPing/HTTPing 延迟测试边界。"""
from __future__ import annotations

import asyncio
import socket
import ssl
import time

from models import LatencyStats, Node

DEFAULT_TCP_TIMEOUT = 1.5
DEFAULT_TCP_SAMPLES = 1
MAX_TCP_SAMPLES = 32
HTTPING_DOMAIN = "speed.cloudflare.com"


async def _tcping_once(node: Node, timeout: float) -> float | None:
    """对 node 执行一次 TCP 握手，返回毫秒延迟；失败返回 None。"""
    start = time.perf_counter()
    writer = None
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(node.ip, node.port), timeout=timeout
        )
        return round((time.perf_counter() - start) * 1000, 2)
    except (OSError, TimeoutError, asyncio.TimeoutError):
        return None
    finally:
        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except (OSError, TimeoutError, asyncio.TimeoutError):
                pass


async def tcping_stats(node: Node, timeout: float, samples: int = 3) -> LatencyStats:
    """执行多次 TCP 探测，返回最小延迟、发送数、接收数和丢包率。"""
    samples = max(1, min(int(samples), MAX_TCP_SAMPLES))
    values = await asyncio.gather(*(_tcping_once(node, timeout) for _ in range(samples)))
    received = [value for value in values if value is not None]
    return LatencyStats(
        latency_ms=min(received) if received else None,
        sent=samples,
        received=len(received),
    )


async def tcping(node: Node, timeout: float, samples: int = 1) -> float | None:
    """对 node 测延迟；samples>1 时并行多次取最小值（抗抖动）。"""
    return (await tcping_stats(node, timeout, samples)).latency_ms


async def _httping_once(node: Node, timeout: float,
                        domain: str = HTTPING_DOMAIN) -> float | None:
    """HTTP TTFB：连接 node → TLS/HTTP 请求 → 首字节时间。"""
    start = time.perf_counter()
    reader = writer = None
    try:
        if node.port == 443:
            context = ssl.create_default_context()
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(node.ip, node.port, ssl=context,
                                        server_hostname=domain),
                timeout=timeout)
        else:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(node.ip, node.port),
                timeout=timeout)
        request = (
            "GET / HTTP/1.1\r\n"
            f"Host: {domain}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        writer.write(request)
        await writer.drain()
        await asyncio.wait_for(reader.read(1), timeout=timeout)
        return round((time.perf_counter() - start) * 1000, 2)
    except (OSError, ssl.SSLError, TimeoutError, asyncio.TimeoutError, ConnectionError):
        return None
    finally:
        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except (OSError, ssl.SSLError, TimeoutError, asyncio.TimeoutError):
                pass


async def httping(node: Node, timeout: float, samples: int = 1,
                  domain: str = HTTPING_DOMAIN) -> float | None:
    """HTTP TTFB；samples>1 时并行多次取最小值。"""
    if samples <= 1:
        return await _httping_once(node, timeout, domain)
    samples = min(samples, MAX_TCP_SAMPLES)
    values = [v for v in await asyncio.gather(
        *(_httping_once(node, timeout, domain) for _ in range(samples))
    ) if v is not None]
    return min(values) if values else None


def positive_worker_count(requested: int, item_count: int) -> int:
    """将并发数钳制到 [1, item_count]，避免空任务或负并发。"""
    return max(1, min(max(1, requested), max(1, item_count)))
