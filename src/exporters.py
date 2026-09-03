# -*- coding: utf-8 -*-
"""测速结果排序与订阅格式导出。"""
from __future__ import annotations

import base64
import csv
import json
from collections.abc import Sequence
from pathlib import Path

from models import SpeedResult

FAST_LABEL = "优选高速 "


def _sort_key(result: SpeedResult, sort: str):
    """按指定键返回排序元组；未知键回退到 speed。"""
    if sort == "latency":
        return (result.latency_ms, -result.speed_mbps)
    if sort == "region":
        return (result.node.region, -result.speed_mbps, result.latency_ms)
    if sort == "score":
        score = result.speed_mbps - result.latency_ms / 100.0
        return (-score, -result.speed_mbps, result.latency_ms)
    if sort != "speed":
        print(f"WARNING: unknown sort key {sort!r}, falling back to 'speed'")
    return (-result.speed_mbps, result.latency_ms)


def _filtered_sorted(results: Sequence[SpeedResult], sort: str,
                     regions: Sequence[str]) -> list[SpeedResult]:
    region_set = {region.upper() for region in regions if region.strip()}
    filtered = results if not region_set else [
        result for result in results if result.node.region.upper() in region_set
    ]
    return sorted(filtered, key=lambda result: _sort_key(result, sort))


def write_results(path: Path, results: Sequence[SpeedResult],
                  sort: str = "speed", regions: Sequence[str] = ()) -> None:
    """写结果文件；按指定键排序，fast 打标签。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = _filtered_sorted(results, sort, regions)
    temp = path.with_name(path.name + ".tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as file:
            for result in ordered:
                label = FAST_LABEL if result.is_fast else ""
                file.write(
                    f"{result.node.raw} [{label}{result.latency_ms}ms | "
                    f"{result.speed_mbps}Mbps]\n"
                )
        temp.replace(path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def export_csv(path: Path, results: Sequence[SpeedResult],
               sort: str = "speed", regions: Sequence[str] = ()) -> None:
    """导出 UTF-8 BOM CSV，方便 Excel 打开。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = _filtered_sorted(results, sort, regions)
    temp = path.with_name(path.name + ".tmp")
    try:
        with temp.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["ip", "port", "region", "latency_ms", "speed_mbps", "is_fast"])
            for result in ordered:
                writer.writerow([
                    result.node.ip, result.node.port, result.node.region,
                    f"{result.latency_ms:.1f}", f"{result.speed_mbps:.2f}",
                    "1" if result.is_fast else "0",
                ])
        temp.replace(path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def export_v2ray(path: Path, results: Sequence[SpeedResult],
                 sort: str = "speed", regions: Sequence[str] = ()) -> None:
    """导出 vmess:// 订阅文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for result in _filtered_sorted(results, sort, regions):
        payload = json.dumps({
            "v": "2",
            "ps": f"CF-{result.node.region}-{result.speed_mbps:.0f}Mbps",
            "add": result.node.ip,
            "port": str(result.node.port),
            "id": "00000000-0000-0000-0000-000000000000",
            "aid": "0", "net": "tcp", "type": "none", "host": "",
            "path": "", "tls": "", "sni": "",
        }, separators=(",", ":"))
        encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
        lines.append(f"vmess://{encoded}")
    with path.open("w", encoding="utf-8", newline="\n") as file:
        file.write("\n".join(lines))
        if lines:
            file.write("\n")


def export_singbox(path: Path, results: Sequence[SpeedResult],
                   sort: str = "speed", regions: Sequence[str] = (),
                   name_prefix: str = "CF") -> None:
    """导出 sing-box outbound JSONL。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for result in _filtered_sorted(results, sort, regions):
        outbound = {
            "type": "vmess",
            "tag": f"{name_prefix}-{result.node.region}-{result.speed_mbps:.0f}M",
            "server": result.node.ip,
            "server_port": result.node.port,
            "uuid": "00000000-0000-0000-0000-000000000000",
            "security": "auto", "alter_id": 0, "tls": {"enabled": False},
        }
        lines.append(json.dumps(outbound, ensure_ascii=False))
    with path.open("w", encoding="utf-8", newline="\n") as file:
        file.write("\n".join(lines))
        if lines:
            file.write("\n")


def _yaml_str(value: str) -> str:
    """将字符串安全表示为 YAML 双引号字符串。"""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def export_clash(path: Path, results: Sequence[SpeedResult],
                 sort: str = "speed", regions: Sequence[str] = (),
                 name_prefix: str = "CF") -> None:
    """导出 Clash YAML 代理节点文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = _filtered_sorted(results, sort, regions)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        file.write("proxies:\n")
        for result in ordered:
            name = f"{name_prefix}-{result.node.region}-{result.speed_mbps:.0f}M"
            file.write(f"  - name: {_yaml_str(name)}\n")
            file.write("    type: vmess\n")
            file.write(f"    server: {result.node.ip}\n")
            file.write(f"    port: {result.node.port}\n")
            file.write("    uuid: 00000000-0000-0000-0000-000000000000\n")
            file.write("    alterId: 0\n")
            file.write("    cipher: auto\n")
            file.write("    tls: false\n")
            file.write("    network: tcp\n")
