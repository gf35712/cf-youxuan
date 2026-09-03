# -*- coding: utf-8 -*-
"""CF 优选共享数据模型。

模型保持无业务依赖，供 CLI、GUI 和后续拆分出的网络/导出模块复用。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Node:
    ip: str
    port: int
    region: str

    @property
    def raw(self) -> str:
        """原始行格式 ip:port#region（IPv6 加方括号）。"""
        ip_txt = f"[{self.ip}]" if ":" in self.ip else self.ip
        return f"{ip_txt}:{self.port}#{self.region}"


@dataclass(frozen=True)
class LatencyStats:
    latency_ms: float | None
    sent: int
    received: int

    @property
    def packet_loss(self) -> float:
        if self.sent <= 0:
            return 100.0
        return round((self.sent - self.received) / self.sent * 100.0, 2)


@dataclass
class TcpResult:
    node: Node
    latency_ms: float
    packet_loss: float = 0.0


@dataclass
class SpeedResult:
    node: Node
    latency_ms: float
    speed_mbps: float
    is_fast: bool
    packet_loss: float = 0.0

