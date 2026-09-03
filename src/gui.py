"""Cloudflare 优选 IP 测速工具 — GUI 外壳（重建版）。

调 update.exe 并传参，解析 stdout 进度（JSON 行 / tqdm 文本 / 阶段关键字），
实时展示日志、进度条、结果列表，并持久化用户配置。
"""
from __future__ import annotations

import json
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path

import update  # noqa: E402  # 复用导出函数

APP_NAME = "CF优选测速工具 · Signal Desk"
WIDTH = 1280
HEIGHT = 800

DEFAULT_TCP_TIMEOUT = 1.5
DEFAULT_TCP_WORKERS = 200  # TCP 并发（原 500，降低提高稳定性）
DEFAULT_SPEED_TIMEOUT = 6
DEFAULT_SPEED_WORKERS = 8  # 测速并发（原 16，降低减少失败率）
DEFAULT_MIN_SPEED = 0  # Mbps；默认 0 = 不过滤，阈值完全由用户通过滑块决定
DEFAULT_TOP = 10
DEFAULT_IPS_URL = "https://zip.cm.edu.kg/all.txt"
DEFAULT_BENCHMARK = "1.1.1.1:443"
DEFAULT_VERIFY_TOP = 0  # 默认关闭二次验证（重测波动会误杀），用户可手动开启
DEFAULT_GUI_SPEED_BYTES = 1 * 1024 * 1024  # GUI 默认快速筛选 1MiB；CLI 保持 4MiB 精测默认
DEFAULT_GUI_FAST_MODE = True  # 保留快速采样参数，但默认完整测试候选池
DEFAULT_GUI_STOP_AFTER_FAST = 0  # 默认完整测试候选池，不提前停止
DEFAULT_GUI_RECHECK_TOP = 50  # GUI 默认复测延迟最好的前 50 个节点
DEFAULT_GUI_RECHECK_SAMPLES = 3
DEFAULT_GUI_MAX_PACKET_LOSS = 20.0  # GUI 默认过滤复测丢包超过 20% 的节点
CANDIDATE_MODE_LABELS = {"分区覆盖": "regional", "全局最快": "global", "自适应": "adaptive"}
CANDIDATE_MODE_KEYS = {v: k for k, v in CANDIDATE_MODE_LABELS.items()}
DEFAULT_GUI_CANDIDATE_MODE = "adaptive"
DEFAULT_GUI_CANDIDATE_LIMIT = 300

DARK_THEME = {
    "bg": "#1a1a1a", "card": "#2a2a2a", "border": "#333333",
    "accent": "#d4a853", "accent_hover": "#c4963a",
    "success": "#6b9f8a", "error": "#e06c75",
    "text": "#e0e0e0", "text_bright": "#f0f0f0", "muted": "#888888",
    "log_bg": "#111111", "log_text": "#bbbbbb", "best_text": "#9ddc9d",
    "progress_bg": "#3a3a3a", "slider_bg": "#444444",
}

LIGHT_THEME = {
    "bg": "#e9e7e2", "card": "#f7f5f1", "border": "#d3d0c9",
    "accent": "#b0763a", "accent_hover": "#96622c",
    "success": "#4c7a63", "error": "#b0403a",
    "text": "#3a3835", "text_bright": "#1f1e1c", "muted": "#8a867e",
    "log_bg": "#f1efe9", "log_text": "#3c3a37", "best_text": "#2c6b45",
    "progress_bg": "#ddd9d1", "slider_bg": "#ccc8bf",
}

# CTk 控件颜色用 (light, dark) 双色元组：切换主题时 customtkinter 自动换色，无需重建 UI
THEME_COLORS = {k: (LIGHT_THEME[k], DARK_THEME[k]) for k in LIGHT_THEME}
# 单色当前主题值：供 ttk.Treeview 等原生控件使用（它们不接受双色元组）
_CURRENT = dict(DARK_THEME)


def apply_theme(name: str) -> str:
    """切换全局主题色（"dark" / "light"）。

    CTk 控件传 THEME_COLORS 元组会自动跟随 appearance_mode；
    _CURRENT 维护单色值供 ttk.Treeview 等原生控件使用。
    """
    global _CURRENT
    name = "light" if name == "light" else "dark"
    _CURRENT.clear()
    _CURRENT.update(LIGHT_THEME if name == "light" else DARK_THEME)
    try:
        import customtkinter as ctk
        ctk.set_appearance_mode(name)
    except Exception:
        pass
    return name


TQDM_PATTERN = re.compile(r"(\d+)%.*?\|\s*(\d+)/(\d+)")

# 排序：显示标签 -> CLI 键
SORT_LABELS = {"按速度": "speed", "按延迟": "latency", "按区域": "region", "综合评分": "score"}
SORT_KEYS = {v: k for k, v in SORT_LABELS.items()}

# 数据源类型：显示标签 -> CLI 模式
SOURCE_LABELS = {"在线列表": "url", "官方IPv4段": "official4", "官方IPv6段": "official6", "本地文件": "file"}
SOURCE_KEYS = {v: k for k, v in SOURCE_LABELS.items()}

# 测速源预设只负责填入地址；真正测速前仍会在日志中显示最终地址。
SPEED_SOURCE_URLS = {
    "自动选择": "",
    "Cloudflare": "https://speed.cloudflare.com/__down",
    "CM提供": "https://cf.090227.xyz/__down",
    "移动专属": "https://speed.okl.abrdns.com/__down",
}
SPEED_SOURCE_LABELS = tuple(SPEED_SOURCE_URLS) + ("自定义",)


def speed_source_url(label: str) -> str | None:
    """将测速源预设名称转换为地址；自定义返回 None。"""
    if label == "自定义":
        return None
    return SPEED_SOURCE_URLS.get(label, "")


def speed_source_preset(url: str | None) -> str:
    """根据已保存地址识别预设，无法匹配时返回“自定义”。"""
    value = str(url or "").strip().rstrip("/")
    for label, preset_url in SPEED_SOURCE_URLS.items():
        if value == preset_url.rstrip("/"):
            return label
    return "自定义" if value else "自动选择"

# 扫描方式：显示标签 -> CLI 键
SCAN_LABELS = {"TCPing (握手)": "tcping", "HTTPing (TTFB)": "httping"}
SCAN_KEYS = {v: k for k, v in SCAN_LABELS.items()}
SCAN_DESCRIPTIONS = {
    "tcping": "TCPing：TCP 握手延迟，适合比较节点连通和基础延迟。",
    "httping": "HTTPing：HTTP 首字节延迟，通常高于 TCPing；两种模式不要直接横比。",
}

# 国家/地区代码 -> 中文名（地区筛选显示用）
COUNTRY_CN = {
    "US": "美国", "HK": "香港", "JP": "日本", "SG": "新加坡", "KR": "韩国",
    "TW": "台湾", "CN": "中国", "MO": "澳门", "DE": "德国", "FR": "法国",
    "GB": "英国", "NL": "荷兰", "CA": "加拿大", "AU": "澳大利亚", "IN": "印度",
    "VN": "越南", "TH": "泰国", "MY": "马来西亚", "ID": "印尼", "PH": "菲律宾",
    "RU": "俄罗斯", "UA": "乌克兰", "PL": "波兰", "CZ": "捷克", "SE": "瑞典",
    "FI": "芬兰", "NO": "挪威", "DK": "丹麦", "IT": "意大利", "ES": "西班牙",
    "PT": "葡萄牙", "CH": "瑞士", "AT": "奥地利", "BE": "比利时", "IE": "爱尔兰",
    "GR": "希腊", "TR": "土耳其", "AE": "阿联酋", "SA": "沙特", "IL": "以色列",
    "BR": "巴西", "MX": "墨西哥", "AR": "阿根廷", "CL": "智利", "ZA": "南非",
    "EG": "埃及", "NG": "尼日利亚", "NZ": "新西兰", "CR": "哥斯达黎加",
    "PA": "巴拿马", "BG": "保加利亚", "RO": "罗马尼亚", "HU": "匈牙利",
    "LT": "立陶宛", "LV": "拉脱维亚", "EE": "爱沙尼亚", "IS": "冰岛",
    "LU": "卢森堡", "SK": "斯洛伐克", "SI": "斯洛文尼亚", "HR": "克罗地亚",
    "RS": "塞尔维亚", "MD": "摩尔多瓦", "GE": "格鲁吉亚", "AZ": "阿塞拜疆",
    "KZ": "哈萨克斯坦", "UZ": "乌兹别克斯坦", "PK": "巴基斯坦", "BD": "孟加拉",
    "LK": "斯里兰卡", "NP": "尼泊尔", "KH": "柬埔寨", "LA": "老挝",
    "MM": "缅甸", "BN": "文莱", "MONG": "蒙古", "IR": "伊朗",
    "QA": "卡塔尔", "KW": "科威特", "OM": "阿曼", "BH": "巴林",
    "JO": "约旦", "LB": "黎巴嫩", "CY": "塞浦路斯", "MT": "马耳他",
}


def region_label(code: str) -> str:
    """地区代码显示为 `中文(代码)`；未知代码原样显示。"""
    code = (code or "").strip().upper()
    if not code:
        return "全部地区"
    return f"{COUNTRY_CN.get(code, code)}({code})"

# 工作目录内允许清理的文件白名单
# ips.txt 是最近一次成功下载的输入列表快照：不属于测速历史，保留它可以在
# 在线源和代理同时不可用时继续当前扫描。每次 TCP 仍由 --no-cache 全量重测。
WORKDIR_CLEANABLE = {"best_ips.txt", "full_ips.txt", "_ips_url.txt", "run.log"}
GUI_LOG_MAX_LINES = 3000
LATENCY_DISPLAY_EVERY = 20
LATENCY_DISPLAY_INTERVAL = 0.15
_SINGLE_INSTANCE_HANDLE = None

# 参数持久化配置文件
CONFIG_FILE = Path.home() / ".cf_ips_scanner" / "gui_config.json"
CONFIG_KEYS = {
    "ips_url": "", "work_dir": "", "update_exe": "",
    "verbose": False,
    "tcp_timeout": DEFAULT_TCP_TIMEOUT, "tcp_workers": DEFAULT_TCP_WORKERS,
    "speed_timeout": DEFAULT_SPEED_TIMEOUT, "speed_workers": DEFAULT_SPEED_WORKERS,
    "speed_bytes": DEFAULT_GUI_SPEED_BYTES,
    "fast_mode": DEFAULT_GUI_FAST_MODE,
    "stop_after_fast": DEFAULT_GUI_STOP_AFTER_FAST,
    "recheck_top": DEFAULT_GUI_RECHECK_TOP, "recheck_samples": DEFAULT_GUI_RECHECK_SAMPLES,
    "max_packet_loss": DEFAULT_GUI_MAX_PACKET_LOSS,
    "candidate_mode": DEFAULT_GUI_CANDIDATE_MODE, "candidate_limit": DEFAULT_GUI_CANDIDATE_LIMIT,
    "min_speed": DEFAULT_MIN_SPEED, "top_per_region": DEFAULT_TOP,
    "sort": "speed", "region": "",
    "csv_export": False, "export_formats": "",
    "speed_url": "", "speed_port": 0, "speed_samples": 1, "interval": 0,
    "benchmark": DEFAULT_BENCHMARK, "benchmark_enabled": True, "verify_top": DEFAULT_VERIFY_TOP,
    "theme": "dark",
    "source_type": "url", "local_file": "",
    "scan_mode": "tcping", "annotate": True, "proxy": "",
    "speed_proxy": "",
    "config_version": 3,  # 保持既有配置版本兼容；proxy 表示列表代理
}



def _coerce_config_value(key: str, value):
    """按 CONFIG_KEYS 默认类型校验/转换配置值；非法类型回退默认。"""
    default = CONFIG_KEYS.get(key)
    try:
        if isinstance(default, bool):
            return bool(value)
        if isinstance(default, (int, float)):
            if isinstance(value, bool):  # bool 是 int 子类，先排除
                return default
            if isinstance(value, (int, float)):
                return value
            f = float(value)  # 字符串数字
            if isinstance(default, int) and f.is_integer():
                return int(f)
            return f
        if isinstance(default, str):
            return str(value)
    except (ValueError, TypeError):
        return default
    return default


def load_config(path: Path | None = None) -> dict:
    """读取 GUI 配置 JSON；文件缺失/损坏/类型非法时回退默认值。"""
    path = path or CONFIG_FILE
    cfg = dict(CONFIG_KEYS)
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k in CONFIG_KEYS:
                    if k in data:
                        cfg[k] = _coerce_config_value(k, data[k])
                # 版本迁移（min_speed 语义变化：v1=Mbps, v2=MB/s, v3=Mbps）
                stored_version = data.get("config_version", 1)
                if "config_version" not in data:
                    # 维持旧配置的版本标记兼容；下面的安全值迁移仍会执行。
                    cfg["config_version"] = 1
                if stored_version == 2:
                    # v2 中 min_speed 是 MB/s 语义，×8 迁移到 Mbps
                    ms = data.get("min_speed")
                    if ms is not None:
                        cfg["min_speed"] = int(ms) * 8  # MB/s -> Mbps
                    cfg["config_version"] = 3
                if stored_version <= 3:
                    # v3 及更早版本曾保存 0.5s/100 候选/10 个早停的
                    # 快速组合；读取时纠正危险组合，避免旧配置继续造成
                    # 低可达率和过小候选池。用户之后仍可在界面主动调整。
                    if float(cfg.get("tcp_timeout", DEFAULT_TCP_TIMEOUT)) < 1.0:
                        cfg["tcp_timeout"] = DEFAULT_TCP_TIMEOUT
                    if cfg.get("candidate_limit") == 100:
                        cfg["candidate_limit"] = DEFAULT_GUI_CANDIDATE_LIMIT
                    if cfg.get("fast_mode") is True and cfg.get("stop_after_fast") == 10:
                        cfg["stop_after_fast"] = DEFAULT_GUI_STOP_AFTER_FAST
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return cfg


def save_config(data: dict, path: Path | None = None) -> bool:
    """保存 GUI 配置 JSON（原子写入）；失败返回 False 不抛异常。"""
    path = path or CONFIG_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({k: data.get(k, v) for k, v in CONFIG_KEYS.items()},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError:
        return False


def parse_progress_line(line: str):
    """解析 update.exe 输出的一行，返回 (progress_value, stage_text, tested, total) 或 None。

    支持两种格式：
      1. tqdm 文本:  "45%|####| 12/34"
      2. JSON 行:   {"progress": 0.45, "stage": "...", "done": 320, "total": 1000}
    """
    m = TQDM_PATTERN.search(line)
    if m:
        pct = int(m.group(1)) / 100
        detail = f"{m.group(2)}/{m.group(3)}"
        return pct, detail, int(m.group(2)), int(m.group(3))
    try:
        obj = json.loads(line.strip())
        if isinstance(obj, dict) and "progress" in obj:
            v = float(obj["progress"])
            stage = obj.get("stage", "")
            done = obj.get("done", 0) or obj.get("tested", 0)
            total = obj.get("total", 0)
            return v, stage, int(done), int(total)
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def progress_stage(line: str):
    """根据输出行内容推断当前阶段。"""
    ll = line.strip().lower()
    if "stage 1/2" in ll or "tcp latency" in ll:
        return 0.02, "TCP 延迟测试..."
    if "stage 2/2" in ll or "download speed" in ll:
        return 0.45, "下载速度测试..."
    if ll.startswith("done"):
        return 1.0, "完成"
    if "fast tagged" in ll:
        return 0.95, "汇总结果..."
    return None


def _resource_path() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).parent


def find_update_exe() -> Path | None:
    """依次在资源目录、本文件目录、PATH 中查找 update.exe。"""
    candidates = [
        _resource_path() / "update.exe",
        Path(__file__).parent / "update.exe",
    ]
    for c in candidates:
        if c.exists():
            return c
    found = shutil.which("update.exe")
    return Path(found) if found else None


def kill_process_tree(proc: subprocess.Popen | None) -> None:
    """Kill the process and its entire child process tree（含 curl 子进程）。"""
    if proc is None or proc.returncode is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        proc.kill()


def resolve_update_exe(path_str: str) -> Path | None:
    """解析用户指定的 update.exe 路径；空/无效则回退自动查找。"""
    p = path_str.strip() if path_str else ""
    if p:
        cand = Path(p)
        if cand.exists():
            return cand
    return find_update_exe()



def build_update_command(exe: Path, work_dir: Path, ips_url: str,
                         params: dict, verbose: bool) -> list[str]:
    """构造调用 update.exe 的命令行；桌面扫描默认每次重新测 TCP。"""
    cmd = [
        str(exe),
        "--json-progress",
        "--no-cache",
        "--verbose" if verbose else "",
        "--tcp-workers", str(int(params["tcp_workers"])),
        "--speed-timeout", str(params["speed_timeout"]),
        "--speed-workers", str(int(params["speed_workers"])),
        "--min-speed", str(float(params["min_speed"])),  # GUI/CLI 都直接 Mbps
        "--top", str(int(params["top_per_region"])),
    ]
    # 扫描方式 + 地区标注
    scan_mode = params.get("scan_mode", "tcping")
    if scan_mode != "tcping":
        cmd += ["--scan-mode", scan_mode]
    if params.get("annotate", True):
        cmd += ["--annotate"]
    # 数据源类型：官方段 / 本地文件 / 在线列表（在线列表沿用 _ips_url.txt 机制）
    source = params.get("source_type", "url")
    if source == "official4":
        cmd += ["--input-mode", "official", "--official-iptype", "4"]
    elif source == "official6":
        cmd += ["--input-mode", "official", "--official-iptype", "6"]
    elif source == "file":
        local = str(params.get("local_file", "")).strip()
        if local:
            cmd += ["-i", local]
    # TCP 超时：0 = 用 CLI 默认；GUI 不允许低于 1 秒，避免旧配置的
    # 0.5 秒在大规模跨境节点扫描时把大量可达节点误判为超时。
    tcp_timeout = float(params.get("tcp_timeout", 0))
    if tcp_timeout > 0:
        cmd += ["--tcp-timeout", str(round(max(1.0, tcp_timeout), 1))]
    candidate_mode = params.get("candidate_mode", "regional")
    if candidate_mode in ("global", "adaptive"):
        cmd += ["--candidate-mode", candidate_mode]
    candidate_limit = _safe_int(params.get("candidate_limit", 0), 0)
    if candidate_limit > 0 and candidate_mode in ("global", "adaptive"):
        cmd += ["--candidate-limit", str(candidate_limit)]
    stop_after_fast = _safe_int(params.get("stop_after_fast", 0), 0)
    if stop_after_fast > 0:
        cmd += ["--stop-after-fast", str(stop_after_fast)]
    recheck_top = _safe_int(params.get("recheck_top", 0), 0)
    if recheck_top > 0:
        cmd += ["--recheck-top", str(recheck_top)]
    recheck_samples = _safe_int(params.get("recheck_samples", 0), 0)
    if recheck_samples > 0:
        cmd += ["--recheck-samples", str(recheck_samples)]
    max_packet_loss = _safe_float(params.get("max_packet_loss", 100), 100)
    if max_packet_loss < 100:
        cmd += ["--max-packet-loss", str(max(0.0, min(100.0, max_packet_loss)))]
    if params.get("sort", "speed") != "speed":
        cmd += ["--sort", params["sort"]]
    if params.get("region", ""):
        cmd += ["--region", params["region"]]
    if params.get("csv_export", False):
        cmd += ["--csv"]
    if params.get("export_formats", ""):
        for fmt in params["export_formats"].replace(",", " ").split():
            cmd += ["--export", fmt.strip()]
    speed_bytes = _safe_int(params.get("speed_bytes", 0), 0)
    if speed_bytes > 0:
        cmd += ["--speed-bytes", str(speed_bytes)]
    if params.get("speed_url", ""):
        cmd += ["--speed-url", params["speed_url"]]
    # GUI 的“代理”是列表代理：CM 在线列表走代理，节点测速默认直连。
    # speed_proxy 只有用户明确填写时才会影响测速。
    if params.get("proxy", ""):
        cmd += ["--input-proxy", params["proxy"]]
    if params.get("speed_proxy", ""):
        cmd += ["--speed-proxy", params["speed_proxy"]]
    if params.get("speed_port", 0):
        cmd += ["--speed-port", str(int(params["speed_port"]))]
    if params.get("speed_samples", 1) > 1:
        cmd += ["--speed-samples", str(int(params["speed_samples"]))]
    interval = params.get("interval", 0)
    if interval:
        cmd += ["--interval", str(int(interval))]
    # 基准测试 / 优选二次验证：与 CLI 默认一致，仅覆盖时传参
    if not params.get("benchmark_enabled", True):
        cmd += ["--no-benchmark"]
    else:
        bench = str(params.get("benchmark", "")).strip()
        if bench and bench != DEFAULT_BENCHMARK:
            cmd += ["--benchmark", bench]
    verify = int(params.get("verify_top", DEFAULT_VERIFY_TOP))
    if verify != DEFAULT_VERIFY_TOP:
        cmd += ["--verify-top", str(verify)]
    return [p for p in cmd if p]


def process_completed(returncode: int | None, saw_done: bool) -> bool:
    """只有 CLI 正常退出且输出最终 Done 才算测速完整结束。"""
    return returncode == 0 and saw_done


def should_display_latency(index: int, now: float, last_display: float,
                           every: int = LATENCY_DISPLAY_EVERY,
                           min_interval: float = LATENCY_DISPLAY_INTERVAL) -> bool:
    """决定是否把 TCP 明细采样显示到 GUI，完整明细仍写入 run.log。"""
    if index <= 1:
        return True
    return index % max(1, every) == 0 or now - last_display >= min_interval


def acquire_single_instance() -> bool:
    """防止桌面版重复启动，多个实例会同时抢占网络和工作目录。"""
    global _SINGLE_INSTANCE_HANDLE
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, "CFYouxuanSpeedTool.SingleInstance")
        if not handle:
            return True
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return False
        _SINGLE_INSTANCE_HANDLE = handle
        return True
    except Exception:
        return True


def smooth_progress(current: float, target: float, step: float = 0.02) -> float:
    """进度平滑：向上缓慢逼近 target（每次最多 step），向下直接跟随。"""
    if target <= current:
        return target
    return min(target, current + step)


RESULT_PATTERN = re.compile(
    r"\[(?P<label>优选高速\s*)?(?P<latency>[\d.]+)ms\s*\|\s*(?P<speed>[\d.]+)Mbps\]"
)


def parse_curl_speed_result(line: str):
    """解析结果行，返回 (latency_ms, speed_mbps, is_fast) 或 None。

    输入形如：`1.1.1.1:443#A [优选高速 12.3ms | 45.6Mbps]`
    """
    m = RESULT_PATTERN.search(line)
    if not m:
        return None
    return (
        float(m.group("latency")),
        float(m.group("speed")),
        bool(m.group("label")),
    )


def _safe_int(value: str, default: int = 0) -> int:
    """安全解析整数字符串；空/非法输入回退默认值。"""
    try:
        return int(str(value).strip() or default)
    except (ValueError, TypeError):
        return default


def _safe_float(value: str, default=None):
    """安全解析浮点数字符串；空/非法输入返回 default（None = 不过滤）。"""
    try:
        s = str(value).strip()
        if not s:
            return default
        return float(s)
    except (ValueError, TypeError):
        return default


def filter_pick_rows(rows, region="", maxlat=None, minspeed=None, only_fast=False):
    """筛选候选行：region 精确匹配，maxlat/minspeed 数值过滤，only_fast 只看优选。"""
    out = []
    for row in rows:
        _ip, reg, lat, spd, fast = row
        if region and region != "\u5168\u90e8" and reg != region:
            continue
        if maxlat is not None and lat > maxlat:
            continue
        if minspeed is not None and spd < minspeed:
            continue
        if only_fast and not fast:
            continue
        out.append(row)
    return out


def sort_pick_rows(rows, col="latency", reverse=False):
    """按列排序候选行：node/region 字符串，latency/speed/fast 数值。"""
    order = {"node": 0, "region": 1, "latency": 2, "speed": 3, "fast": 4}
    idx = order.get(col, 2)
    return sorted(rows, key=lambda r: r[idx], reverse=reverse)


def sort_full_results_by_latency(content: str) -> str:
    """按延迟升序稳定排序结果文本行；无法解析的行保持原顺序排在末尾。"""
    parsed = []
    tail = []
    for line in content.splitlines():
        r = parse_curl_speed_result(line)
        if r is None:
            tail.append(line)
        else:
            parsed.append((r[0], line))
    ordered = [line for _lat, line in sorted(parsed, key=lambda item: item[0])]
    ordered.extend(tail)
    if ordered:
        return "\n".join(ordered) + "\n"
    return ""


def summarize_results(text: str):
    """统计结果文本：返回 (fast_count, total_count, best_latency_ms, best_speed_mbps)。"""
    fast = 0
    total = 0
    best_latency = None
    best_speed = None
    for line in text.splitlines():
        r = parse_curl_speed_result(line)
        if r is None:
            continue
        total += 1
        if r[2]:
            fast += 1
        if best_latency is None or r[0] < best_latency:
            best_latency = r[0]
        if best_speed is None or r[1] > best_speed:
            best_speed = r[1]
    return fast, total, best_latency, best_speed



# ---------------------------------------------------------------------------
# GUI（customtkinter 延迟导入）
# ---------------------------------------------------------------------------
class App:
    def __init__(self, root):
        import customtkinter as ctk

        self.ctk = ctk
        self.root = root
        self.running = False
        self.process = None
        self._run_generation = 0
        self._finished_generation = -1
        self._run_saw_done = False
        self._run_tail = deque(maxlen=12)
        self._run_log_path = None
        self._run_latency_seen = 0
        self._run_latency_last_display = 0.0
        self.work_dir = Path.cwd() / "_cf_run"
        self._cfg = load_config()
        self._theme = self._cfg.get("theme", "dark")
        apply_theme(self._theme)
        if self._cfg.get("work_dir"):
            self.work_dir = Path(self._cfg["work_dir"])
        self._params = {
            "tcp_timeout": self._cfg.get("tcp_timeout", DEFAULT_TCP_TIMEOUT),
            "tcp_workers": self._cfg.get("tcp_workers", DEFAULT_TCP_WORKERS),
            "speed_timeout": self._cfg.get("speed_timeout", DEFAULT_SPEED_TIMEOUT),
            "speed_workers": self._cfg.get("speed_workers", DEFAULT_SPEED_WORKERS),
            "min_speed": float(self._cfg.get("min_speed", DEFAULT_MIN_SPEED)),  # Mbps
            "top_per_region": self._cfg.get("top_per_region", DEFAULT_TOP),
        }
        self._build_ui()
        self._restore_values()
        # 线程安全 UI 调度：worker 线程只入队，主线程轮询执行
        self._ui_queue = queue.Queue()
        self._live_latency = {}  # 实时结果：node -> latency
        self._poll_timer = None
        self._bar_timer = None
        self._poll_ui()
        # IP 定位：后台线程获取公网 IP + 位置（仅展示参考，不自动过滤区域）
        if hasattr(self, "_ip_bar"):
            threading.Thread(target=_fetch_my_ip,
                             args=(self._ip_bar, self.ctk, None, self._ui),
                             daemon=True).start()

    # -- 线程安全 UI 调度 -------------------------------------------------
    def _poll_ui(self):
        """主线程轮询：drain 线程安全队列中的 UI 任务。"""
        import queue as _q
        while True:
            try:
                fn = self._ui_queue.get_nowait()
            except _q.Empty:
                break
            try:
                fn()
            except Exception:
                pass
        try:
            self._poll_timer = self.root.after(50, self._poll_ui)
        except Exception:
            self._poll_timer = None

    def _close_ui(self):
        """取消所有主线程定时器（窗口销毁前调用，避免残留 after 报错）。"""
        for attr in ("_poll_timer", "_bar_timer"):
            t = getattr(self, attr, None)
            if t is not None:
                try:
                    self.root.after_cancel(t)
                except Exception:
                    pass
                setattr(self, attr, None)

    def _ui(self, fn):
        """线程安全调度 UI 任务：主线程直接执行，worker 线程入队。"""
        try:
            if threading.current_thread() is threading.main_thread():
                try:
                    fn()
                except Exception:
                    pass
                return
            self._ui_queue.put(fn)
        except Exception:
            pass

    # -- UI 构建 ----------------------------------------------------------
    def _auto_fill_region(self, location):
        """根据 IP 定位手动推荐区域（不再自动触发；用户可在高级选项自行填写区域过滤）。"""
        current = self._region_entry.get().strip()
        if current:
            return  # 用户已手动输入，不覆盖
        suggested = suggest_regions(location)
        if suggested:
            self._region_entry.delete(0, "end")
            self._region_entry.insert(0, suggested)

    def _toggle_adv(self):
        """展开/收起高级选项。"""
        self._adv_expanded = not self._adv_expanded
        try:
            if self._adv_expanded:
                self._adv_frame.pack(fill="x", pady=1)
                self._adv_btn.configure(text="\u25BE 高级选项")
            else:
                self._adv_frame.pack_forget()
                self._adv_btn.configure(text="\u25B8 高级选项")
        except Exception:
            pass

    # -- 实时状态 ----------------------------------------------------------
    def _refresh_ip(self):
        """点击 IP 标签时重新获取本机 IP 和位置。"""
        try:
            self._ip_bar.configure(text="\U0001F4CD 本机 IP: 获取中... (点击刷新)")
        except Exception:
            pass
        threading.Thread(target=lambda: _fetch_my_ip(self._ip_bar, self.ctk, None, self._ui),
                         daemon=True).start()

    def _update_live(self, line):
        """从 update 输出行提取当前测速节点，更新顶部实时标签 + 实时追加结果。"""
        # [LAT] 1.1.1.1:443#HK -> 12.3 ms   或   [SPEED] 1.1.1.1:443#HK -> 45.6 Mbps FAST
        m = re.search(r"\[(LAT|SPEED)\] (\S+) -> ([\d.]+) (ms|Mbps)", line)
        if not m:
            return
        kind, node, val, unit = m.group(1), m.group(2), m.group(3), m.group(4)

        # 收集每个节点的延迟（供结果行显示）
        if kind == "LAT":
            self._live_latency[node] = val

        # 测速完成：实时追加结果到结果列表（不等全部完成）
        if kind == "SPEED":
            is_fast = "FAST" in line
            latency = self._live_latency.get(node, "--")
            result_line = f"{node} [{'优选高速 ' if is_fast else ''}{latency}ms | {val}Mbps]"
            self._ui(lambda: self._live_append_result(is_fast, result_line))

        text = f"{node} -> {val} {unit}"
        # 节流：最多每 150ms 更新一次
        now = time.monotonic()
        if now - getattr(self, "_live_last", 0.0) < 0.15:
            return
        self._live_last = now

        def _do():
            try:
                self._live_label.configure(text=text)
            except Exception:
                pass
        # worker 线程调用：入队由主线程执行
        self._ui(_do)

    def _live_append_result(self, is_fast: bool, line: str):
        """主线程：把一条测速结果实时插入结果文本框。"""
        try:
            if is_fast:
                self._best_text.insert("end", line + "\n")
            self._full_text.insert("end", line + "\n")
        except Exception:
            pass

    # -- 主题切换 ----------------------------------------------------------
    def _toggle_theme(self):
        """切换深色/浅色主题：先呈现换色，辅助任务（表格样式/保存配置）延后一帧执行，
        让切换更跟手。"""
        self._theme = "light" if self._theme == "dark" else "dark"
        apply_theme(self._theme)
        try:
            self._theme_btn.configure(
                text="\U0001F31E" if self._theme == "light" else "\U0001F319")
        except Exception:
            pass
        # 非视觉任务延后，不阻塞换色帧
        try:
            self.root.after_idle(self._apply_tree_style)
        except Exception:
            pass
        try:
            self.root.after_idle(self._save_config)
        except Exception:
            pass

    def _apply_tree_style(self):
        """刷新 ttk.Treeview 样式与标签色（原生控件不跟随 appearance_mode）。"""
        try:
            from tkinter import ttk as _ttk
            style = _ttk.Style()
            style.theme_use("clam")
            style.configure(
                "CF.Treeview",
                background=_CURRENT["log_bg"], foreground=_CURRENT["text"],
                fieldbackground=_CURRENT["log_bg"], rowheight=24,
                font=("Consolas", 11), borderwidth=0)
            style.configure(
                "CF.Treeview.Heading",
                background=_CURRENT["card"], foreground=_CURRENT["text_bright"],
                font=("Microsoft YaHei UI", 10, "bold"))
            style.map("CF.Treeview",
                      background=[("selected", _CURRENT["accent"])],
                      foreground=[("selected", _CURRENT["bg"])])
            self._pick_tree.tag_configure("fast", foreground=_CURRENT["best_text"])
            self._pick_tree.tag_configure("sel", background=_CURRENT["accent"],
                                          foreground=_CURRENT["bg"])
        except Exception:
            pass

    def _restore_values(self):
        """重建 UI 后恢复所有控件值（从 _cfg 读取）。"""
        if self._cfg.get("ips_url"):
            self._ips_url_var.set(self._cfg["ips_url"])
        self._source_var.set(SOURCE_KEYS.get(self._cfg.get("source_type", "url"), "在线列表"))
        if self._cfg.get("local_file"):
            self._local_file_var.set(self._cfg["local_file"])
        self._scan_var.set(SCAN_KEYS.get(self._cfg.get("scan_mode", "tcping"), "TCPing (握手)"))
        self._annotate_var.set(bool(self._cfg.get("annotate", True)))
        if self._cfg.get("update_exe"):
            self._update_exe_var.set(self._cfg["update_exe"])
        self._verbose_var.set(bool(self._cfg.get("verbose", True)))
        self._sort_var.set(SORT_KEYS.get(self._cfg.get("sort", "speed"), "按速度"))
        self._region_entry.delete(0, "end")
        self._region_entry.insert("end", self._cfg.get("region", ""))
        self._csv_var.set(bool(self._cfg.get("csv_export", False)))
        self._export_entry.delete(0, "end")
        self._export_entry.insert("end", self._cfg.get("export_formats", ""))
        self._speed_url_entry.delete(0, "end")
        self._speed_url_entry.insert("end", self._cfg.get("speed_url", ""))
        self._proxy_entry.delete(0, "end")
        self._proxy_entry.insert("end", self._cfg.get("proxy", ""))
        sp = self._cfg.get("speed_port", 0)
        if sp:
            self._speed_port_entry.delete(0, "end")
            self._speed_port_entry.insert("end", str(sp))
        ival = self._cfg.get("interval", 0)
        if ival:
            self._interval_entry.delete(0, "end")
            self._interval_entry.insert("end", str(ival))
        smp = int(self._cfg.get("speed_samples", 1))
        if smp > 1:
            self._samples_entry.delete(0, "end")
            self._samples_entry.insert("end", str(smp))
        for k, s in self._sliders.items():
            v = self._cfg.get(k)
            if v is None:
                v = self._params.get(k)
            if v is not None:
                # min_speed 单元已在 load_config 中统一迁移到 Mbps，此处无需额外处理
                s.set(v)
        self._theme_btn.configure(text="\U0001F31E" if self._theme == "light" else "\U0001F319")


    def _build_ui(self):
        ctk = self.ctk
        self.root.title(APP_NAME)
        # 窗口自适应：大屏用默认尺寸，小屏不超过屏幕 92% 宽 / 88% 高
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        win_w = min(WIDTH, int(sw * 0.92))
        win_h = min(HEIGHT, int(sh * 0.88))
        # 窗口位置：水平居中、垂直偏上（屏幕 1/3 处），避免落在屏幕下方
        pos_x = max(0, (sw - win_w) // 2)
        pos_y = max(0, (sh - win_h) // 3)
        self.root.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")
        self.root.minsize(min(1040, win_w), min(680, win_h))
        self.root.configure(fg_color=THEME_COLORS["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._ips_url_var = ctk.StringVar(value=DEFAULT_IPS_URL)
        self._source_var = ctk.StringVar(
            value=SOURCE_KEYS.get(self._cfg.get("source_type", "url"), "在线列表"))
        self._local_file_var = ctk.StringVar(value=str(self._cfg.get("local_file", "")))
        self._scan_var = ctk.StringVar(
            value=SCAN_KEYS.get(self._cfg.get("scan_mode", "tcping"), "TCPing (握手)"))
        self._annotate_var = ctk.BooleanVar(value=bool(self._cfg.get("annotate", True)))
        # 筛选候选 tab 状态
        self._pick_region_code = ""  # "" = 全部地区；否则为国家代码
        self._pick_region_options = ["全部"]
        self._pick_fast_var = ctk.BooleanVar(value=False)
        self._pick_rows = []
        self._pick_sort_col = "latency"
        self._pick_sort_rev = False
        self._work_dir_var = ctk.StringVar(value=str(self.work_dir))
        self._update_exe_var = ctk.StringVar(value="")  # 空 = 自动查找
        self._verbose_var = ctk.BooleanVar(value=True)

        # 布局：main 容器 -> content(左 520px + 右) + 底部栏(状态|进度条|百分比 同一行)
        self._main = ctk.CTkFrame(self.root, fg_color="transparent")
        self._main.pack(fill="both", expand=True, padx=16, pady=12)

        # 顶部 IP 定位栏 + 主题切换
        top_bar = ctk.CTkFrame(self._main, fg_color=THEME_COLORS["card"], corner_radius=6, height=26)
        top_bar.pack(fill="x", pady=(0, 6))
        top_bar.pack_propagate(False)
        self._ip_bar = ctk.CTkLabel(
            top_bar, text="\U0001F4CD 本机 IP: 获取中... (点击刷新)",
            font=ctk.CTkFont(size=11), anchor="w",
            text_color=THEME_COLORS["muted"], cursor="hand2")
        self._ip_bar.pack(side="left", padx=(10, 0), fill="y")
        self._ip_bar.bind("<Button-1>", lambda e: self._refresh_ip())
        self._live_label = ctk.CTkLabel(
            top_bar, text="", font=ctk.CTkFont(size=11), anchor="e",
            text_color=THEME_COLORS["accent"])
        self._live_label.pack(side="right", padx=(10, 6))
        theme_icon = "\U0001F31E" if self._theme == "light" else "\U0001F319"
        self._theme_btn = ctk.CTkButton(
            top_bar, text=theme_icon, width=32, height=22,
            font=ctk.CTkFont(size=13), fg_color="transparent",
            hover_color=THEME_COLORS["border"],
            text_color=THEME_COLORS["muted"],
            command=self._toggle_theme)
        self._theme_btn.pack(side="right", padx=(0, 6))

        content = ctk.CTkFrame(self._main, fg_color="transparent")
        content.pack(fill="both", expand=True)
        content.grid_columnconfigure(0, weight=0, minsize=520)
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(0, weight=1)

        self._build_left(content)
        self._build_right(content)

        bottom = ctk.CTkFrame(self._main, fg_color="transparent", height=26)
        bottom.pack(fill="x", pady=(2, 0))
        bottom.pack_propagate(False)

        self.status_label = ctk.CTkLabel(
            bottom, text="就绪", font=ctk.CTkFont(size=13),
            text_color=THEME_COLORS["muted"])
        self.status_label.pack(side="left", padx=(0, 8))

        self._progress_bar = ctk.CTkProgressBar(
            bottom, mode="determinate", fg_color=THEME_COLORS["border"],
            progress_color=THEME_COLORS["accent"], height=8, corner_radius=4)
        self._progress_bar.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self._progress_bar.set(0)

        self._progress_label = ctk.CTkLabel(
            bottom, text="", font=ctk.CTkFont(size=11),
            text_color=THEME_COLORS["muted"], width=100, anchor="e")
        self._progress_label.pack(side="right")

    def _build_left(self, parent):
        ctk = self.ctk
        left = ctk.CTkScrollableFrame(
            parent, fg_color=THEME_COLORS["card"], corner_radius=10,
            scrollbar_button_color=THEME_COLORS["accent"],
            scrollbar_button_hover_color=THEME_COLORS["accent_hover"])
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.grid_columnconfigure(0, weight=1)

        # -- 卡片 1：IP 数据源 --
        s = ctk.CTkFrame(left, fg_color=THEME_COLORS["card"], corner_radius=10)
        s.pack(fill="x", padx=10, pady=(10, 4))
        ctk.CTkLabel(s, text="\U0001F310 IP 数据源",
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=THEME_COLORS["text_bright"]).pack(anchor="w", pady=(8, 4))
        ctk.CTkLabel(s, text="数据源类型", font=ctk.CTkFont(size=12),
                     text_color=THEME_COLORS["muted"]).pack(anchor="w")
        self._source_menu = ctk.CTkOptionMenu(
            s, values=list(SOURCE_LABELS.keys()), variable=self._source_var,
            command=lambda _v: self._rebuild_source_widgets(),
            font=ctk.CTkFont(size=12),
            fg_color=THEME_COLORS["bg"], button_color=THEME_COLORS["accent"],
            button_hover_color=THEME_COLORS["accent_hover"],
            dropdown_fg_color=THEME_COLORS["card"],
            dropdown_text_color=THEME_COLORS["text"],
            dropdown_hover_color=THEME_COLORS["border"],
            text_color=THEME_COLORS["text"], height=30)
        self._source_menu.pack(fill="x", pady=(2, 6))
        self._src_frame = ctk.CTkFrame(s, fg_color="transparent")
        self._src_frame.pack(fill="x")
        self._rebuild_source_widgets()

        # -- 卡片 2：测速参数（标签行 + 滑块行 28px + 浮动徽标）--
        ps = ctk.CTkFrame(left, fg_color=THEME_COLORS["card"], corner_radius=10)
        ps.pack(fill="x", padx=10, pady=4)
        ctk.CTkLabel(ps, text="\u2699\uFE0F 测速参数",
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=THEME_COLORS["text_bright"]).pack(anchor="w", pady=(8, 8))
        self._sliders = {}
        param_defs = [
            ("tcp_timeout", "TCP 超时", "秒", 0, 10, self._params["tcp_timeout"], 0.1),
            ("tcp_workers", "TCP 并发数", "", 1, 1000, self._params["tcp_workers"], 10),
            ("speed_timeout", "测速超时", "秒", 1, 30, self._params["speed_timeout"], 1),
            ("speed_workers", "测速并发数", "", 1, 64, self._params["speed_workers"], 1),
            ("min_speed", "最低高速", "Mbps", 0, 400, self._params["min_speed"], 1),
            ("top_per_region", "每区候选数", "", 1, 50, self._params["top_per_region"], 1),
        ]
        for key, label, unit, vmin, vmax, default, step in param_defs:
            frame = ctk.CTkFrame(ps, fg_color="transparent")
            frame.pack(fill="x", pady=5)
            ctk.CTkLabel(frame, text=label, font=ctk.CTkFont(size=13),
                         text_color=THEME_COLORS["text"]).pack(anchor="w")
            row = ctk.CTkFrame(frame, fg_color="transparent", height=28)
            row.pack(fill="x")
            row.pack_propagate(False)
            ls = LabeledSlider(row, vmin, vmax, int((vmax - vmin) / step),
                               default, unit)
            ls.pack(fill="x", expand=True)
            self._sliders[key] = ls


        # -- 卡片 3：选项（verbose + 高级选项）--
        os_ = ctk.CTkFrame(left, fg_color=THEME_COLORS["card"], corner_radius=10)
        os_.pack(fill="x", padx=10, pady=4)
        ctk.CTkLabel(os_, text="\U0001F4CB 选项",
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=THEME_COLORS["text_bright"]).pack(anchor="w", pady=(6, 6))
        cb_style = dict(font=ctk.CTkFont(size=13), text_color=THEME_COLORS["text"],
                        fg_color=THEME_COLORS["accent"],
                        hover_color=THEME_COLORS["accent_hover"],
                        checkmark_color=THEME_COLORS["bg"],
                        border_color=THEME_COLORS["border"])
        self._verbose_check = ctk.CTkCheckBox(
            os_, text="详细输出 (verbose)", variable=self._verbose_var, **cb_style)
        self._verbose_check.pack(anchor="w", pady=2)

        # -- 高级选项（默认收起，点击展开）--
        self._adv_expanded = False
        self._adv_btn = ctk.CTkButton(
            os_, text="\u25B8 高级选项", font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="transparent", hover_color=THEME_COLORS["border"],
            text_color=THEME_COLORS["muted"], anchor="w", height=24,
            command=self._toggle_adv)
        self._adv_btn.pack(fill="x", pady=(8, 2))
        self._adv_frame = ctk.CTkFrame(os_, fg_color="transparent")
        self._adv_frame.grid_columnconfigure(1, weight=1)
        adv = self._adv_frame
        # 排序
        ctk.CTkLabel(adv, text="排序", font=ctk.CTkFont(size=12),
                     text_color=THEME_COLORS["text"]).grid(row=0, column=0, sticky="w", padx=(0, 4))
        self._sort_var = ctk.StringVar(value=SORT_KEYS.get(CONFIG_KEYS["sort"], "按速度"))
        self._sort_menu = ctk.CTkOptionMenu(adv, values=list(SORT_LABELS.keys()),
            variable=self._sort_var, font=ctk.CTkFont(size=12),
            fg_color=THEME_COLORS["bg"], button_color=THEME_COLORS["accent"],
            button_hover_color=THEME_COLORS["accent_hover"],
            dropdown_fg_color=THEME_COLORS["card"],
            dropdown_text_color=THEME_COLORS["text"],
            dropdown_hover_color=THEME_COLORS["border"],
            text_color=THEME_COLORS["text"])
        self._sort_menu.grid(row=0, column=1, sticky="ew", pady=1)
        # 区域过滤
        ctk.CTkLabel(adv, text="区域过滤", font=ctk.CTkFont(size=12),
                     text_color=THEME_COLORS["text"]).grid(row=1, column=0, sticky="w", padx=(0, 4))
        self._region_entry = ctk.CTkEntry(adv, font=ctk.CTkFont(size=12),
            fg_color=THEME_COLORS["bg"], border_color=THEME_COLORS["border"],
            text_color=THEME_COLORS["text"], height=26,
            placeholder_text="留空=不过滤")
        self._region_entry.grid(row=1, column=1, sticky="ew", pady=1)
        ctk.CTkLabel(adv, text="逗号分隔，如 HK,SG", font=ctk.CTkFont(size=10),
                     text_color=THEME_COLORS["muted"]).grid(row=1, column=2, padx=4)
        # CSV 导出
        self._csv_var = ctk.BooleanVar(value=False)
        self._csv_check = ctk.CTkCheckBox(adv, text="CSV 导出", variable=self._csv_var, **cb_style)
        self._csv_check.grid(row=2, column=0, columnspan=2, sticky="w", pady=1)
        # 导出格式
        ctk.CTkLabel(adv, text="导出格式", font=ctk.CTkFont(size=12),
                     text_color=THEME_COLORS["text"]).grid(row=3, column=0, sticky="w", padx=(0, 4))
        self._export_entry = ctk.CTkEntry(adv, font=ctk.CTkFont(size=12),
            fg_color=THEME_COLORS["bg"], border_color=THEME_COLORS["border"],
            text_color=THEME_COLORS["text"], height=26)
        self._export_entry.grid(row=3, column=1, sticky="ew", pady=1)
        ctk.CTkLabel(adv, text="csv/v2ray/clash/singbox", font=ctk.CTkFont(size=10),
                     text_color=THEME_COLORS["muted"]).grid(row=3, column=2, padx=4)
        # 测速 URL / 端口
        ctk.CTkLabel(adv, text="测速 URL", font=ctk.CTkFont(size=12),
                     text_color=THEME_COLORS["text"]).grid(row=4, column=0, sticky="w", padx=(0, 4))
        self._speed_url_entry = ctk.CTkEntry(adv, font=ctk.CTkFont(size=12),
            fg_color=THEME_COLORS["bg"], border_color=THEME_COLORS["border"],
            text_color=THEME_COLORS["text"], height=26)
        self._speed_url_entry.grid(row=4, column=1, columnspan=2, sticky="ew", pady=1)
        ctk.CTkLabel(adv, text="测速端口", font=ctk.CTkFont(size=12),
                     text_color=THEME_COLORS["text"]).grid(row=5, column=0, sticky="w", padx=(0, 4))
        self._speed_port_entry = ctk.CTkEntry(adv, font=ctk.CTkFont(size=12),
            fg_color=THEME_COLORS["bg"], border_color=THEME_COLORS["border"],
            text_color=THEME_COLORS["text"], height=26, width=80)
        self._speed_port_entry.grid(row=5, column=1, sticky="w", pady=1)
        ctk.CTkLabel(adv, text="0=自动", font=ctk.CTkFont(size=10),
                     text_color=THEME_COLORS["muted"]).grid(row=5, column=2, padx=4)
        # 定时重跑
        ctk.CTkLabel(adv, text="定时重跑(秒)", font=ctk.CTkFont(size=12),
                     text_color=THEME_COLORS["text"]).grid(row=6, column=0, sticky="w", padx=(0, 4))
        self._interval_entry = ctk.CTkEntry(adv, font=ctk.CTkFont(size=12),
            fg_color=THEME_COLORS["bg"], border_color=THEME_COLORS["border"],
            text_color=THEME_COLORS["text"], height=26, width=80)
        self._interval_entry.grid(row=6, column=1, sticky="w", pady=1)
        ctk.CTkLabel(adv, text="0=只跑一次", font=ctk.CTkFont(size=10),
                     text_color=THEME_COLORS["muted"]).grid(row=6, column=2, padx=4)
        # 采样次数（多次取中位数，更抗抖动）
        ctk.CTkLabel(adv, text="测速采样", font=ctk.CTkFont(size=12),
                     text_color=THEME_COLORS["text"]).grid(row=7, column=0, sticky="w", padx=(0, 4))
        self._samples_entry = ctk.CTkEntry(adv, font=ctk.CTkFont(size=12),
            fg_color=THEME_COLORS["bg"], border_color=THEME_COLORS["border"],
            text_color=THEME_COLORS["text"], height=26, width=80)
        self._samples_entry.grid(row=7, column=1, sticky="w", pady=1)
        ctk.CTkLabel(adv, text="1-8 取中位数", font=ctk.CTkFont(size=10),
                     text_color=THEME_COLORS["muted"]).grid(row=7, column=2, padx=4)
        # 扫描方式
        ctk.CTkLabel(adv, text="扫描方式", font=ctk.CTkFont(size=12),
                     text_color=THEME_COLORS["text"]).grid(row=8, column=0, sticky="w", padx=(0, 4))
        self._scan_menu = ctk.CTkOptionMenu(adv, values=list(SCAN_LABELS.keys()),
            variable=self._scan_var, font=ctk.CTkFont(size=12),
            fg_color=THEME_COLORS["bg"], button_color=THEME_COLORS["accent"],
            button_hover_color=THEME_COLORS["accent_hover"],
            dropdown_fg_color=THEME_COLORS["card"],
            dropdown_text_color=THEME_COLORS["text"],
            dropdown_hover_color=THEME_COLORS["border"],
            text_color=THEME_COLORS["text"])
        self._scan_menu.grid(row=8, column=1, sticky="ew", pady=1)
        ctk.CTkLabel(adv, text="握手/TTFB", font=ctk.CTkFont(size=10),
                     text_color=THEME_COLORS["muted"]).grid(row=8, column=2, padx=4)
        # 地区标注（短文字 + 右侧小字说明，避免列宽裁剪）
        self._annotate_check = ctk.CTkCheckBox(
            adv, text="地区标注", variable=self._annotate_var,
            font=ctk.CTkFont(size=12), text_color=THEME_COLORS["text"],
            fg_color=THEME_COLORS["accent"], hover_color=THEME_COLORS["accent_hover"],
            checkmark_color=THEME_COLORS["bg"], border_color=THEME_COLORS["border"])
        self._annotate_check.grid(row=9, column=1, sticky="w", pady=1)
        ctk.CTkLabel(adv, text="仅标注不筛选", font=ctk.CTkFont(size=10),
                     text_color=THEME_COLORS["muted"]).grid(row=9, column=2, padx=4)
        # 代理（下载 IP 列表 + 测速共用；空 = 直连）
        ctk.CTkLabel(adv, text="代理", font=ctk.CTkFont(size=12),
                     text_color=THEME_COLORS["text"]).grid(row=10, column=0, sticky="w", padx=(0, 4))
        self._proxy_entry = ctk.CTkEntry(adv, font=ctk.CTkFont(size=12),
            fg_color=THEME_COLORS["bg"], border_color=THEME_COLORS["border"],
            text_color=THEME_COLORS["text"], height=26)
        self._proxy_entry.grid(row=10, column=1, sticky="ew", pady=1)
        ctk.CTkLabel(adv, text="仅下载列表用, 测速直连", font=ctk.CTkFont(size=10),
                     text_color=THEME_COLORS["muted"]).grid(row=10, column=2, padx=4)

        # -- 卡片 4：工作目录 --
        wd = ctk.CTkFrame(left, fg_color=THEME_COLORS["card"], corner_radius=10)
        wd.pack(fill="x", padx=10, pady=4)
        ctk.CTkLabel(wd, text="\U0001F4C1 工作目录",
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=THEME_COLORS["text_bright"]).pack(anchor="w", pady=(6, 4))
        wd_row = ctk.CTkFrame(wd, fg_color="transparent")
        wd_row.pack(fill="x", pady=(0, 6))
        self._work_dir_entry = ctk.CTkEntry(
            wd_row, textvariable=self._work_dir_var,
            font=ctk.CTkFont(size=12, family="Consolas"),
            fg_color=THEME_COLORS["bg"], border_color=THEME_COLORS["border"],
            text_color=THEME_COLORS["text"], height=32)
        self._work_dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._browse_btn = ctk.CTkButton(
            wd_row, text="选择...", font=ctk.CTkFont(size=12),
            fg_color=THEME_COLORS["border"], hover_color="#555555",
            text_color=THEME_COLORS["text"], width=70, height=32,
            corner_radius=6, command=self._browse_work_dir)
        self._browse_btn.pack(side="right")

        # -- 卡片 5：操作 --
        as_ = ctk.CTkFrame(left, fg_color=THEME_COLORS["card"], corner_radius=10)
        as_.pack(fill="x", padx=10, pady=4)
        ctk.CTkLabel(as_, text="\u25B6 操作",
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=THEME_COLORS["text_bright"]).pack(anchor="w", pady=(6, 6))
        self._start_btn = ctk.CTkButton(
            as_, text="\U0001F680  开始测速", font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=THEME_COLORS["accent"], hover_color=THEME_COLORS["accent_hover"],
            text_color=THEME_COLORS["bg"], height=42, corner_radius=8,
            command=self._start_run)
        self._start_btn.pack(fill="x", pady=2)
        self._stop_btn = ctk.CTkButton(
            as_, text="\u23F9  停止", font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=THEME_COLORS["border"], hover_color="#555555",
            text_color=THEME_COLORS["text"], height=42, corner_radius=8,
            state="disabled", command=self._stop_run)
        self._stop_btn.pack(fill="x", pady=(2, 10))

    def _rebuild_source_widgets(self):
        """按数据源类型重建输入区：在线列表 / 官方段 / 本地文件。"""
        ctk = self.ctk
        for child in self._src_frame.winfo_children():
            child.destroy()
        src = SOURCE_LABELS.get(self._source_var.get(), "url")
        if src == "file":
            row = ctk.CTkFrame(self._src_frame, fg_color="transparent")
            row.pack(fill="x", pady=(2, 8))
            self._local_file_entry = ctk.CTkEntry(
                row, textvariable=self._local_file_var,
                font=ctk.CTkFont(size=12, family="Consolas"),
                fg_color=THEME_COLORS["bg"], border_color=THEME_COLORS["border"],
                text_color=THEME_COLORS["text"], height=32)
            self._local_file_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
            self._browse_src_btn = ctk.CTkButton(
                row, text="选择...", font=ctk.CTkFont(size=12),
                fg_color=THEME_COLORS["border"], hover_color="#555555",
                text_color=THEME_COLORS["text"], width=70, height=32,
                corner_radius=6, command=self._browse_input_file)
            self._browse_src_btn.pack(side="right")
        elif src in ("official4", "official6"):
            ip_txt = "IPv4" if src == "official4" else "IPv6"
            ctk.CTkLabel(
                self._src_frame,
                text=f"自动从 Cloudflare 官方 {ip_txt} IP 段随机采样候选 IP（每段 5 个）",
                font=ctk.CTkFont(size=11), text_color=THEME_COLORS["muted"],
                wraplength=270, justify="left").pack(anchor="w", pady=(2, 8))
        else:
            ctk.CTkLabel(self._src_frame, text="下载地址", font=ctk.CTkFont(size=12),
                         text_color=THEME_COLORS["muted"]).pack(anchor="w")
            self._ips_url_entry = ctk.CTkEntry(
                self._src_frame, textvariable=self._ips_url_var,
                font=ctk.CTkFont(size=12, family="Consolas"),
                fg_color=THEME_COLORS["bg"], border_color=THEME_COLORS["border"],
                text_color=THEME_COLORS["text"], height=32)
            self._ips_url_entry.pack(fill="x", pady=(2, 8))

    def _browse_input_file(self):
        """选择本地 IP 列表文件（txt/csv）。"""
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="选择 IP 列表文件",
            filetypes=[("文本文件", "*.txt *.csv"), ("所有文件", "*.*")])
        if path:
            self._local_file_var.set(path)

    def _build_right(self, parent):
        ctk = self.ctk
        right = ctk.CTkFrame(parent, fg_color=THEME_COLORS["card"], corner_radius=10)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)

        font13 = ctk.CTkFont(size=13, family="Consolas")
        self._tabs = ctk.CTkTabview(
            right, fg_color=THEME_COLORS["card"],
            segmented_button_fg_color=THEME_COLORS["bg"],
            segmented_button_selected_color=THEME_COLORS["accent"],
            segmented_button_selected_hover_color=THEME_COLORS["accent_hover"],
            text_color=THEME_COLORS["text"])
        self._tabs.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self._tabs._segmented_button.configure(font=ctk.CTkFont(size=13))

        log_tab = self._tabs.add("\U0001F4DC 运行日志")
        log_tab.grid_rowconfigure(0, weight=1)
        log_tab.grid_columnconfigure(0, weight=1)
        self._log_text = ctk.CTkTextbox(
            log_tab, font=font13,
            fg_color=THEME_COLORS["log_bg"], text_color=THEME_COLORS["log_text"],
            corner_radius=6, border_width=0, wrap="word")
        self._log_text.grid(row=0, column=0, sticky="nsew")
        lf = ctk.CTkFrame(log_tab, fg_color="transparent")
        lf.grid(row=1, column=0, sticky="e", pady=(6, 0))
        self._flat_btn(lf, "清空日志",
                       lambda: self._log_text.delete("0.0", "end"))

        best_tab = self._tabs.add("\U0001F680 高速优选")
        best_tab.grid_rowconfigure(0, weight=1)
        best_tab.grid_columnconfigure(0, weight=1)
        self._best_text = ctk.CTkTextbox(
            best_tab, font=font13,
            fg_color=THEME_COLORS["log_bg"], text_color=THEME_COLORS["best_text"],
            corner_radius=6, border_width=0, wrap="word")
        self._best_text.grid(row=0, column=0, sticky="nsew")
        bf = ctk.CTkFrame(best_tab, fg_color="transparent")
        bf.grid(row=1, column=0, sticky="e", pady=(6, 0))
        self._primary_btn(bf, "\U0001F4CB 复制全部",
                          lambda: self._copy_output(self._best_text))

        full_tab = self._tabs.add("\U0001F4E6 全部可用")
        full_tab.grid_rowconfigure(0, weight=1)
        full_tab.grid_columnconfigure(0, weight=1)
        self._full_text = ctk.CTkTextbox(
            full_tab, font=font13,
            fg_color=THEME_COLORS["log_bg"], text_color=THEME_COLORS["log_text"],
            corner_radius=6, border_width=0, wrap="word")
        self._full_text.grid(row=0, column=0, sticky="nsew")
        ff = ctk.CTkFrame(full_tab, fg_color="transparent")
        ff.grid(row=1, column=0, sticky="e", pady=(6, 0))
        self._primary_btn(ff, "\U0001F4CB 复制全部",
                          lambda: self._copy_output(self._full_text))

        pick_tab = self._tabs.add("\U0001F5D1 筛选候选")
        pick_tab.grid_rowconfigure(1, weight=1)
        pick_tab.grid_columnconfigure(0, weight=1)
        from tkinter import ttk as _ttk
        style = _ttk.Style()
        style.theme_use("clam")
        style.configure(
            "CF.Treeview",
            background=_CURRENT["log_bg"], foreground=_CURRENT["text"],
            fieldbackground=_CURRENT["log_bg"], rowheight=24,
            font=("Consolas", 11), borderwidth=0)
        style.configure(
            "CF.Treeview.Heading",
            background=_CURRENT["card"], foreground=_CURRENT["text_bright"],
            font=("Microsoft YaHei UI", 10, "bold"))
        style.map("CF.Treeview",
                  background=[("selected", _CURRENT["accent"])],
                  foreground=[("selected", _CURRENT["bg"])])
        self._pick_tree = _ttk.Treeview(
            pick_tab, columns=("node", "region", "latency", "speed", "fast"),
            show="tree headings", selectmode="extended", style="CF.Treeview")
        # #0 列显示选择框 ☐/☑，点击任意行切换勾选（无需 Ctrl/Shift）
        self._pick_tree.heading("#0", text="", command=self._pick_select_all)
        self._pick_tree.column("#0", width=40, anchor="center", stretch=False)
        for col, text, width, anchor in (
            ("node", "节点 ip:port", 250, "w"),
            ("region", "地区", 70, "center"),
            ("latency", "延迟ms", 90, "e"),
            ("speed", "速度Mbps", 100, "e"),
            ("fast", "优选", 60, "center"),
        ):
            self._pick_tree.heading(col, text=text,
                                    command=lambda c=col: self._pick_sort_toggle(c))
            self._pick_tree.column(col, width=width, anchor=anchor,
                                   stretch=(col == "node"))
        self._pick_tree.bind("<Button-1>", self._on_pick_click)
        self._pick_selected = set()
        self._pick_row_map = {}

        # 筛选行：地区 / 延迟 / 速度 / 仅优选
        fbar = ctk.CTkFrame(pick_tab, fg_color="transparent")
        fbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkLabel(fbar, text="地区", font=ctk.CTkFont(size=12),
                     text_color=THEME_COLORS["text"]).pack(side="left")
        self._pick_region_btn = ctk.CTkButton(
            fbar, text="\u25BE 全部地区", command=self._toggle_region_menu,
            font=ctk.CTkFont(size=12), width=200, height=28,
            fg_color=THEME_COLORS["bg"], hover_color=THEME_COLORS["border"],
            border_width=1, border_color=THEME_COLORS["border"],
            text_color=THEME_COLORS["text"])
        self._pick_region_btn.pack(side="left", padx=(4, 12))
        ctk.CTkLabel(fbar, text="延迟≤", font=ctk.CTkFont(size=12),
                     text_color=THEME_COLORS["text"]).pack(side="left")
        self._pick_maxlat_entry = ctk.CTkEntry(
            fbar, width=64, height=28, font=ctk.CTkFont(size=12, family="Consolas"),
            fg_color=THEME_COLORS["bg"], border_color=THEME_COLORS["border"],
            text_color=THEME_COLORS["text"])
        self._pick_maxlat_entry.pack(side="left", padx=(4, 4))
        self._pick_maxlat_entry.bind("<KeyRelease>", self._on_pick_filter_key)
        ctk.CTkLabel(fbar, text="ms", font=ctk.CTkFont(size=10),
                     text_color=THEME_COLORS["muted"]).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(fbar, text="速度≥", font=ctk.CTkFont(size=12),
                     text_color=THEME_COLORS["text"]).pack(side="left")
        self._pick_minspeed_entry = ctk.CTkEntry(
            fbar, width=64, height=28, font=ctk.CTkFont(size=12, family="Consolas"),
            fg_color=THEME_COLORS["bg"], border_color=THEME_COLORS["border"],
            text_color=THEME_COLORS["text"])
        self._pick_minspeed_entry.pack(side="left", padx=(4, 4))
        self._pick_minspeed_entry.bind("<KeyRelease>", self._on_pick_filter_key)
        ctk.CTkLabel(fbar, text="Mbps", font=ctk.CTkFont(size=10),
                     text_color=THEME_COLORS["muted"]).pack(side="left", padx=(0, 12))
        self._pick_fast_check = ctk.CTkCheckBox(
            fbar, text="仅优选", variable=self._pick_fast_var,
            command=self._apply_filter, font=ctk.CTkFont(size=12),
            text_color=THEME_COLORS["text"], fg_color=THEME_COLORS["accent"],
            hover_color=THEME_COLORS["accent_hover"],
            checkmark_color=THEME_COLORS["bg"], border_color=THEME_COLORS["border"])
        self._pick_fast_check.pack(side="left", padx=(0, 12))
        self._flat_btn(fbar, "清除", self._pick_filter_reset)
        ctk.CTkLabel(fbar, text="点击列头排序 · 多选后复制/导出", font=ctk.CTkFont(size=10),
                     text_color=THEME_COLORS["muted"]).pack(side="left", padx=10)

        self._pick_tree.grid(row=1, column=0, sticky="nsew")
        self._pick_tree.tag_configure("fast", foreground=_CURRENT["best_text"])
        self._pick_tree.tag_configure("sel", background=_CURRENT["accent"],
                                      foreground=_CURRENT["bg"])
        pf = ctk.CTkFrame(pick_tab, fg_color="transparent")
        pf.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self._primary_btn(pf, "\U0001F4CB 复制所选", self._copy_picked)
        self._primary_btn(pf, "\U0001F4E4 导出所选", self._export_picked)
        self._flat_btn(pf, "\u5168\u9009", self._pick_select_all)
        self._flat_btn(pf, "\u6e05\u7a7a", self._pick_clear_all)
        self._flat_btn(pf, "刷新", self._reload_pick_tree)
        ctk.CTkLabel(pf, text="点击行勾选 · 导出格式取高级选项", font=ctk.CTkFont(size=10),
                     text_color=THEME_COLORS["muted"]).pack(side="left", padx=10)

    def _browse_work_dir(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(initialdir=self._work_dir_var.get())
        if d:
            self._work_dir_var.set(d)

    def _browse_update_exe(self):
        from tkinter import filedialog
        f = filedialog.askopenfilename(
            title="选择 update.exe", filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")])
        if f:
            self._update_exe_var.set(f)


    def _collect_params(self) -> dict:
        """收集运行参数：滑块 + 高级选项 + 基准/二次验证。"""
        params = {k: s.get() for k, s in self._sliders.items()}
        params.update({
            "sort": SORT_LABELS.get(self._sort_var.get(), "speed"),
            "region": self._region_entry.get().strip(),
            "csv_export": self._csv_var.get(),
            "export_formats": self._export_entry.get().strip(),
            "speed_url": self._speed_url_entry.get().strip(),
            "proxy": self._proxy_entry.get().strip(),
            "speed_proxy": self._cfg.get("speed_proxy", ""),
            "speed_port": _safe_int(self._speed_port_entry.get(), 0),
            "interval": _safe_int(self._interval_entry.get(), 0),
            "speed_samples": max(1, min(_safe_int(self._samples_entry.get(), 1), 8)),
            "benchmark": str(self._cfg.get("benchmark", DEFAULT_BENCHMARK)),
            "benchmark_enabled": bool(self._cfg.get("benchmark_enabled", True)),
            "verify_top": int(self._cfg.get("verify_top", DEFAULT_VERIFY_TOP)),
            "source_type": SOURCE_LABELS.get(self._source_var.get(), "url"),
            "local_file": self._local_file_var.get().strip(),
            "scan_mode": SCAN_LABELS.get(self._scan_var.get(), "tcping"),
            "annotate": bool(self._annotate_var.get()),
        })
        return params

    def _start_run(self):
        ctk = self.ctk
        if self.running:
            return
        self.work_dir = Path(self._work_dir_var.get())
        try:
            self.work_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._append_log(f"\u274C 错误: 无法创建工作目录 {self.work_dir}\n")
            self._finish_run(False)
            return
        self.running = True
        self._run_generation += 1
        generation = self._run_generation
        self._finished_generation = -1
        self._run_saw_done = False
        self._run_tail.clear()
        self._run_log_path = self.work_dir / "run.log"
        self._run_latency_seen = 0
        self._run_latency_last_display = 0.0

        self._start_btn.configure(state="disabled", text="\u23F3 运行中...")
        self._stop_btn.configure(state="normal")
        self.status_label.configure(text="运行中...", text_color=THEME_COLORS["accent"])
        self._log_text.delete("0.0", "end")
        self._best_text.delete("0.0", "end")
        self._full_text.delete("0.0", "end")
        self._progress_bar.set(0)
        self._progress_label.configure(text="")
        try:
            self._live_label.configure(text="")
        except Exception:
            pass

        # 只清理白名单文件，不删除用户其他文件
        for f in self.work_dir.iterdir():
            if f.is_file() and f.name in WORKDIR_CLEANABLE:
                try:
                    f.unlink()
                except OSError:
                    pass

        exe = resolve_update_exe(self._update_exe_var.get())
        if exe is None:
            self._append_log("\u274C 错误: 找不到 update.exe\n")
            self._finish_run(False)
            return

        url = self._ips_url_var.get().strip()
        src_key = SOURCE_LABELS.get(self._source_var.get(), "url")
        if src_key == "file" and not self._local_file_var.get().strip():
            self._append_log("\u274C 错误: 请选择本地 IP 列表文件\n")
            self._finish_run(False)
            return
        if src_key == "url":
            try:
                (self.work_dir / "_ips_url.txt").write_text(url, encoding="utf-8")
            except OSError as exc:
                self._append_log(f"\u274C 错误: 无法写入 _ips_url.txt: {exc}\n")
                self._finish_run(False)
                return

        params = self._collect_params()
        cmd = build_update_command(exe, self.work_dir, url, params,
                                   self._verbose_var.get())
        # 主线程快照当前配置（_finish_run 在 worker 线程调用时不能再读 tkinter 变量）
        self._cfg_snapshot = {
            "ips_url": url,
            "source_type": src_key,
            "local_file": self._local_file_var.get().strip(),
            "work_dir": str(self.work_dir),
            "update_exe": self._update_exe_var.get().strip(),
            "verbose": self._verbose_var.get(),
            **params,
        }
        self._append_log(f"$ {' '.join(cmd)}\n")
        self._append_log("\u2500" * 60 + "\n")

        t = threading.Thread(target=self._run_process, args=(cmd, generation), daemon=True)
        t.start()

    def _run_process(self, cmd, generation=None):
        """在后台运行 CLI；EOF 不等于完成，必须看到 CLI 的 Done。"""
        if generation is None:
            generation = self._run_generation
        proc = None
        saw_done = False
        tail = deque(maxlen=12)
        log_file = None
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1, cwd=str(self.work_dir),
                creationflags=subprocess.CREATE_NO_WINDOW
                if sys.platform == "win32" else 0,
            )
            if generation != self._run_generation or not self.running:
                kill_process_tree(proc)
                return
            self.process = proc
            try:
                log_file = self.work_dir.joinpath("run.log").open("w", encoding="utf-8", newline="\n")
            except OSError:
                log_file = None
            for line in iter(proc.stdout.readline, ""):
                if generation != self._run_generation or not self.running:
                    break
                line = line.rstrip("\r\n")
                if line.strip() == "Done":
                    saw_done = True
                if line.strip():
                    tail.append(line)
                    if log_file is not None:
                        log_file.write(line + "\n")
                        log_file.flush()
                # JSON 进度行只驱动进度条，不刷日志框
                if parse_progress_line(line):
                    self._try_progress(line)
                    continue
                if line.startswith("[LAT] "):
                    # 每条 TCP 结果都更新内存中的延迟映射，保证后续
                    # [SPEED] 行能显示对应延迟；日志框只采样展示，避免
                    # 15,000 行逐条插入 Tk 导致卡顿。完整明细在 run.log。
                    self._run_latency_seen += 1
                    self._update_live(line)
                    now = time.monotonic()
                    if should_display_latency(
                            self._run_latency_seen, now,
                            self._run_latency_last_display):
                        self._run_latency_last_display = now
                        self._append_log(line)
                else:
                    self._update_live(line)
                    self._append_log(line)
                self._try_progress(line)
            returncode = proc.wait()
            if generation != self._run_generation:
                return
            completed = process_completed(returncode, saw_done)
            self._finish_run(completed, generation=generation, returncode=returncode,
                             saw_done=saw_done, tail=tuple(tail))
        except Exception as e:
            if generation != self._run_generation:
                return
            self._append_log(f"\u274C 错误: {e}\n")
            self._finish_run(False, generation=generation, returncode=None,
                             saw_done=False, tail=tuple(tail))
        finally:
            if log_file is not None:
                try:
                    log_file.close()
                except OSError:
                    pass

    def _try_progress(self, line):
        r = parse_progress_line(line)
        if r:
            self._update_bar(r[0], r[1], r[2], r[3])
            return
        r = progress_stage(line)
        if r:
            self._update_bar(r[0], r[1])

    def _update_bar(self, v, t, tested=0, total=0):
        # 可能被 worker 线程调用：入队由主线程执行
        self._ui(lambda: self._do_update_bar(v, t, tested, total))

    def _do_update_bar(self, v, t, tested=0, total=0):
        self._bar_target = max(0.0, min(1.0, float(v)))
        self._bar_text = t
        pct = f" ({tested * 100 // total}%)" if total > 0 else ""
        stats = f" | 已测 {tested}/{total}{pct}" if total > 0 else ""
        self._bar_stats = stats
        try:
            self._progress_label.configure(text=f"{t}{stats}")
        except Exception:
            pass
        if not getattr(self, "_bar_ticking", False):
            self._bar_ticking = True
            self._bar_tick()

    def _bar_tick(self):
        cur = self._progress_bar.get()
        nxt = smooth_progress(cur, self._bar_target)
        self._progress_bar.set(nxt)
        if self._bar_text is not None:
            stats = getattr(self, "_bar_stats", "")
            self._progress_label.configure(text=f"{self._bar_text}{stats}")
        if nxt < self._bar_target - 1e-6:
            try:
                self._bar_timer = self.root.after(40, self._bar_tick)
            except Exception:
                self._bar_timer = None
        else:
            self._bar_ticking = False
            self._bar_timer = None

    def _stop_run(self):
        generation = self._run_generation
        self.running = False
        proc = self.process
        if proc:
            kill_process_tree(proc)
        self._append_log("\n\u23F9 已手动停止\n")
        self._finish_run(False, generation=generation, returncode=None,
                         saw_done=False, tail=tuple(self._run_tail), manual_stop=True)

    def _save_config(self):
        """保存配置：合并快照（运行参数）与当前控件值（用户最新调整）。"""
        params = {k: s.get() for k, s in self._sliders.items()}
        current = {
            "ips_url": self._ips_url_var.get().strip(),
            "work_dir": self._work_dir_var.get().strip(),
            "update_exe": self._update_exe_var.get().strip(),
            "verbose": self._verbose_var.get(),
            "sort": SORT_LABELS.get(self._sort_var.get(), "speed"),
            "region": self._region_entry.get().strip(),
            "csv_export": self._csv_var.get(),
            "export_formats": self._export_entry.get().strip(),
            "speed_url": self._speed_url_entry.get().strip(),
            "proxy": self._proxy_entry.get().strip(),
            "speed_proxy": self._cfg.get("speed_proxy", ""),
            "speed_port": _safe_int(self._speed_port_entry.get(), 0),
            "interval": _safe_int(self._interval_entry.get(), 0),
            "speed_samples": max(1, min(_safe_int(self._samples_entry.get(), 1), 8)),
            "source_type": SOURCE_LABELS.get(self._source_var.get(), "url"),
            "local_file": self._local_file_var.get().strip(),
            "scan_mode": SCAN_LABELS.get(self._scan_var.get(), "tcping"),
            "annotate": bool(self._annotate_var.get()),
            **params,
        }
        snap = getattr(self, "_cfg_snapshot", None)
        if snap is not None:
            # 快照保持运行参数（benchmark/verify 等无控件配置），控件值覆盖用户手动调整
            data = {**snap, **current, "theme": self._theme}
        else:
            data = {**current, "theme": self._theme}
        save_config(data)


    def _finish_run(self, ok, *, generation=None, returncode=None,
                    saw_done=False, tail=(), manual_stop=False):
        # worker 线程调用：只更新状态，UI 工作交回主线程（线程安全队列）
        if generation is None:
            generation = self._run_generation
        if generation != self._run_generation or self._finished_generation == generation:
            return
        self._finished_generation = generation
        self.running = False
        self.process = None
        self._run_saw_done = saw_done
        self._run_tail = deque(tail, maxlen=12)
        self._ui(lambda: self._finish_run_ui(
            ok, returncode=returncode, saw_done=saw_done,
            tail=tuple(tail), manual_stop=manual_stop))

    def _finish_run_ui(self, ok, *, returncode=None, saw_done=False,
                       tail=(), manual_stop=False):
        self._save_config()
        self._start_btn.configure(state="normal", text="\U0001F680  开始测速")
        if ok:
            self.status_label.configure(text="\u2705 完成", text_color=THEME_COLORS["success"])
            self._update_bar(1, "完成")
            self._load_results()
            self._append_log(self._summary_line())
        else:
            if manual_stop:
                message = "\u23F9 已停止"
            elif saw_done:
                message = f"\u274C 测速失败（退出码 {returncode}）"
            else:
                message = "\u274C 测速进程异常中止（未收到 Done）"
            self.status_label.configure(text=message, text_color=THEME_COLORS["error"])
            if not manual_stop:
                self._append_log(
                    f"\n\u274C 测速进程异常结束：exit_code={returncode}, "
                    f"saw_done={saw_done}\n"
                )
                if tail:
                    self._append_log("最后日志：\n" + "\n".join(tail[-8:]) + "\n")
        self._stop_btn.configure(state="disabled")
        # 运行结束清空实时节点标签，避免残留
        try:
            self._live_label.configure(text="")
        except Exception:
            pass

    def _summary_line(self):
        """结果摘要行，如：✅ 优选 3/5 个 · 最低延迟 10.2ms · 最快 88.5Mbps"""
        best_path = self.work_dir / "best_ips.txt"
        try:
            content = best_path.read_text(encoding="utf-8") if best_path.exists() else ""
        except (OSError, UnicodeDecodeError):
            content = ""
        fast, total, lat, speed = summarize_results(content)
        if total == 0:
            return "\U0001F4CA 本次运行无可用结果\n"
        parts = [f"\U0001F4CA 优选 {fast}/{total} 个"]
        if lat is not None:
            parts.append(f"最低延迟 {lat}ms")
        if speed is not None:
            parts.append(f"最快 {speed}Mbps")
        return " \u00B7 ".join(parts) + "\n"

    def _load_results(self):
        best = self.work_dir / "best_ips.txt"
        full = self.work_dir / "full_ips.txt"
        for box, path in ((self._best_text, best), (self._full_text, full)):
            if not path.exists():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                content = ""
            box.delete("0.0", "end")
            text = sort_full_results_by_latency(content) if box is self._full_text else content
            box.insert("end", text)
        self._reload_pick_tree()

    # -- 候选筛选（待筛选列表：标注地区，用户自行选择）--
    @staticmethod
    def _split_node(node_part: str) -> tuple[str, int]:
        """把 `1.2.3.4:443` / `[::1]:443` 拆成 (ip, port)。"""
        s = node_part.strip()
        if s.startswith("["):
            ip, _, rest = s[1:].partition("]")
            port = rest[1:] if rest.startswith(":") else ""
        else:
            ip, _, port = s.rpartition(":")
        try:
            return ip, int(port)
        except (ValueError, TypeError):
            return ip, 0

    def _picked_results(self) -> list:
        """从勾选行（_pick_selected）构造 update.SpeedResult 列表。"""
        out = []
        row_map = getattr(self, "_pick_row_map", {})
        for iid in getattr(self, "_pick_selected", set()):
            row = row_map.get(iid)
            if row is None:
                continue
            ip_port, reg, lat, spd, fast = row
            ip, port = self._split_node(ip_port)
            if not ip or port <= 0:
                continue
            out.append(update.SpeedResult(
                node=update.Node(ip, port, reg),
                latency_ms=lat, speed_mbps=spd, is_fast=fast))
        return out

    def _reload_pick_tree(self):
        """从 full_ips.txt 加载全部结果到内存缓存，再应用筛选刷新表格。"""
        self._pick_rows = []
        self._pick_row_map = {}
        path = self.work_dir / "full_ips.txt"
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                content = ""
            for line in content.splitlines():
                r = parse_curl_speed_result(line)
                if r is None:
                    continue
                node_part = line.split("[", 1)[0].strip()
                ip_port, _, region = node_part.rpartition("#")
                row = (ip_port, region, r[0], r[1], bool(r[2]))
                self._pick_rows.append(row)
                self._pick_row_map[f"{ip_port}#{region}"] = row
        self._apply_filter()

    def _on_pick_filter_key(self, _event=None):
        """筛选输入防抖：停止输入 250ms 后刷新表格与地区选项。"""
        after_id = getattr(self, "_pick_filter_after", None)
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass
        self._pick_filter_after = self.root.after(250, self._apply_filter)

    def _toggle_region_menu(self):
        """弹出/关闭地区滚动下拉（最多显示 8 行，滚轮查看其余）。"""
        if getattr(self, "_region_menu_win", None) is not None:
            self._close_region_menu()
            return
        ctk = self.ctk
        btn = self._pick_region_btn
        # 弹出前先刷新：确保地区选项 = 当前筛选结果的国家
        self._apply_filter()
        options = getattr(self, "_pick_region_options", ["全部"])
        win = ctk.CTkToplevel(self.root, fg_color=THEME_COLORS["card"])
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        x = btn.winfo_rootx()
        y = btn.winfo_rooty() + btn.winfo_height() + 2
        row_h = 30
        max_rows = 8
        visible = max(1, min(len(options), max_rows))
        w = max(btn.winfo_width(), 140)
        h = visible * row_h + 8
        win.geometry(f"{w}x{h}+{x}+{y}")
        sf = ctk.CTkScrollableFrame(
            win, fg_color=THEME_COLORS["card"], corner_radius=0,
            width=w, height=h)
        sf.pack(fill="both", expand=True)
        for opt in options:
            code = "" if opt == "全部" else opt
            txt = "全部地区" if opt == "全部" else region_label(opt)
            b = ctk.CTkButton(
                sf, text=txt, height=row_h, corner_radius=0, anchor="w",
                font=ctk.CTkFont(size=12),
                fg_color=THEME_COLORS["card"], hover_color=THEME_COLORS["border"],
                text_color=THEME_COLORS["text"],
                command=lambda c=code, t=txt: self._set_region(c, t))
            b.pack(fill="x", padx=1, pady=1)
        self._region_menu_win = win

    def _close_region_menu(self):
        win = getattr(self, "_region_menu_win", None)
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
            self._region_menu_win = None

    def _set_region(self, code, _label=None):
        """选择地区：更新状态并重新筛选（按钮文字由 _apply_filter 统一更新）。"""
        self._pick_region_code = code
        self._close_region_menu()
        self._apply_filter()

    def _on_pick_click(self, event):
        """点击表格行：切换勾选（点列头排序不受影响）。"""
        region = self._pick_tree.identify("region", event.x, event.y)
        if region == "heading":
            return
        iid = self._pick_tree.identify_row(event.y)
        if not iid:
            return
        self._toggle_pick(iid)

    def _toggle_pick(self, iid):
        """勾选/取消勾选一行。"""
        selected = getattr(self, "_pick_selected", set())
        if iid in selected:
            selected.discard(iid)
            tags = [t for t in self._pick_tree.item(iid, "tags") if t != "sel"]
            self._pick_tree.item(iid, text="\u2610", tags=tuple(tags))
        else:
            selected.add(iid)
            tags = list(self._pick_tree.item(iid, "tags"))
            if "sel" not in tags:
                tags.insert(0, "sel")
            self._pick_tree.item(iid, text="\u2611", tags=tuple(tags))

    def _pick_select_all(self):
        """全选当前表格所有行。"""
        selected = getattr(self, "_pick_selected", set())
        for iid in self._pick_tree.get_children():
            selected.add(iid)
            tags = list(self._pick_tree.item(iid, "tags"))
            if "sel" not in tags:
                tags.insert(0, "sel")
            self._pick_tree.item(iid, text="\u2611", tags=tuple(tags))

    def _pick_clear_all(self):
        """清空所有勾选。"""
        selected = getattr(self, "_pick_selected", set())
        selected.clear()
        for iid in self._pick_tree.get_children():
            tags = [t for t in self._pick_tree.item(iid, "tags") if t != "sel"]
            self._pick_tree.item(iid, text="\u2610", tags=tuple(tags))

    def _pick_filter_reset(self):
        """清除筛选条件，恢复显示全部。"""
        try:
            self._pick_region_code = ""
            self._pick_maxlat_entry.delete(0, "end")
            self._pick_minspeed_entry.delete(0, "end")
            self._pick_fast_var.set(False)
        except Exception:
            pass
        self._pick_sort_col = "latency"
        self._pick_sort_rev = False
        self._apply_filter()

    def _pick_sort_toggle(self, col):
        """点击列头：第一次升序，再次点击反向。"""
        if getattr(self, "_pick_sort_col", "") == col:
            self._pick_sort_rev = not getattr(self, "_pick_sort_rev", False)
        else:
            self._pick_sort_col = col
            self._pick_sort_rev = False
        self._apply_filter()

    def _apply_filter(self):
        """按 地区/延迟≤/速度≥/仅优选 过滤缓存行并刷新表格。"""
        try:
            for item in self._pick_tree.get_children():
                self._pick_tree.delete(item)
        except Exception:
            return
        maxlat = _safe_float(self._pick_maxlat_entry.get())
        minspeed = _safe_float(self._pick_minspeed_entry.get())
        only_fast = bool(self._pick_fast_var.get())
        # 第一步：先按 延迟/速度/仅优选 过滤，得到候选集
        pre = filter_pick_rows(getattr(self, "_pick_rows", []),
                               region="", maxlat=maxlat,
                               minspeed=minspeed, only_fast=only_fast)
        # 地区下拉只显示"当前候选集"里的国家（中文标注），联动更新
        codes = sorted({r[1] for r in pre if r[1]})
        self._pick_region_options = ["全部"] + codes
        # 第二步：地区过滤（若当前选择的地区已不在候选中，自动回退全部）
        region = getattr(self, "_pick_region_code", "")
        if region and region not in codes:
            region = ""
            self._pick_region_code = ""
            try:
                self._pick_region_btn.configure(text="全部地区")
            except Exception:
                pass
        shown = filter_pick_rows(pre, region=region)
        shown = sort_pick_rows(shown,
                               getattr(self, "_pick_sort_col", "latency"),
                               getattr(self, "_pick_sort_rev", False))
        selected = getattr(self, "_pick_selected", set())
        # 地区按钮：未选具体地区时，显示当前筛选结果包含的地区概况
        try:
            btn = self._pick_region_btn
            if region:
                btn.configure(text=f"\u25BE {region_label(region)}")
            elif not codes:
                btn.configure(text="\u25BE 全部地区")
            elif len(codes) <= 2:
                btn.configure(text="\u25BE " + " \u00b7 ".join(region_label(c) for c in codes))
            else:
                btn.configure(
                    text=f"\u25BE {region_label(codes[0])} \u00b7 {region_label(codes[1])} \u7b49{len(codes)}\u533a")
        except Exception:
            pass
        for row in shown:
            ip_port, reg, lat, spd, fast = row
            iid = f"{ip_port}#{reg}"
            sel = iid in selected
            tags = []
            if sel:
                tags.append("sel")
            if fast:
                tags.append("fast")
            self._pick_tree.insert("", "end", iid=iid,
                                   text="☑" if sel else "☐",
                                   tags=tuple(tags), values=(
                ip_port, reg or "—", f"{lat:.1f}", f"{spd:.2f}",
                "✓" if fast else ""))

    def _copy_picked(self):
        """复制选中节点（ip:port#region 每行一个）到剪贴板。"""
        rows = []
        for r in self._picked_results():
            rows.append(r.node.raw)
        if not rows:
            self._flash_status("⚠️ 请先在筛选候选里选择节点")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(rows))
        self._flash_status(f"✅ 已复制 {len(rows)} 个节点")

    def _export_picked(self):
        """导出选中节点：格式取高级选项 export_formats，默认 v2ray。"""
        results = self._picked_results()
        if not results:
            self._flash_status("⚠️ 请先在筛选候选里选择节点")
            return
        fmt_txt = self._export_entry.get().strip().lower()
        formats = [f for f in fmt_txt.replace(",", " ").split() if f]
        if not formats:
            formats = ["v2ray"]
        written = []
        for fmt in formats:
            try:
                if fmt == "csv":
                    p = self.work_dir / "picked.csv"
                    update.export_csv(p, results)
                elif fmt == "clash":
                    p = self.work_dir / "picked.clash.yaml"
                    update.export_clash(p, results)
                elif fmt == "singbox":
                    p = self.work_dir / "picked.singbox.json"
                    update.export_singbox(p, results)
                elif fmt == "v2ray":
                    p = self.work_dir / "picked.v2ray.txt"
                    update.export_v2ray(p, results)
                else:
                    continue
                written.append(p.name)
            except Exception as exc:  # noqa: BLE001
                self._append_log(f"\u274C 导出 {fmt} 失败: {exc}\n")
        if written:
            self._flash_status("✅ 已导出: " + ", ".join(written))
            self._append_log(f"\U0001F4E4 导出所选节点 -> {', '.join(str(self.work_dir / w) for w in written)}\n")
        else:
            self._flash_status("⚠️ 未导出（请检查导出格式）")

    def _copy_output(self, textbox=None):
        """从指定 textbox 复制内容，带"已复制到剪贴板"提示。"""
        if textbox is None:
            textbox = self._best_text
        c = textbox.get("0.0", "end").strip()
        if c:
            self.root.clipboard_clear()
            self.root.clipboard_append(c)
            self._flash_status("已复制到剪贴板")

    def _flat_btn(self, parent, text, command, side="left"):
        """原版扁平按钮：透明底、边框线、muted 文字（默认横向排布）。"""
        ctk = self.ctk
        ctk.CTkButton(
            parent, text=text, font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1,
            border_color=THEME_COLORS["muted"],
            text_color=THEME_COLORS["muted"],
            hover_color=THEME_COLORS["border"],
            width=90, height=28, corner_radius=6,
            command=command).pack(side=side)

    def _primary_btn(self, parent, text, command, side="left"):
        """原版主按钮：success 底色、bg 色文字（默认横向排布）。"""
        ctk = self.ctk
        ctk.CTkButton(
            parent, text=text, font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=THEME_COLORS["success"], hover_color="#5a8d74",
            text_color=THEME_COLORS["bg"],
            width=110, height=28, corner_radius=6,
            command=command).pack(side=side)

    def _append_log(self, text):
        # 可能被 worker 线程调用：入队由主线程执行（跨线程 root.after 不可靠）
        self._ui(lambda: self._log_insert(text))

    def _log_insert(self, text):
        try:
            # 清理 ANSI 颜色码/回车等控制字符，避免日志框乱码
            clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
            clean = clean.replace("\r", "\n")
            if clean and not clean.endswith("\n"):
                clean += "\n"
            self._log_text.insert("end", clean)
            # Tk Text 逐行插入数万条 TCP 明细会越来越慢；完整明细已由
            # _run_process 写入 run.log，界面只保留最近一段用于观察。
            line_count = int(self._log_text.index("end-1c").split(".")[0])
            if line_count > GUI_LOG_MAX_LINES:
                self._log_text.delete("1.0", f"{line_count - GUI_LOG_MAX_LINES + 1}.0")
            self._log_text.see("end")
        except Exception:
            pass

    def _flash_status(self, msg):
        orig = self.status_label.cget("text_color")
        self.status_label.configure(text=msg, text_color=THEME_COLORS["error"])
        self.root.after(1500, lambda: self.status_label.configure(text_color=orig))

    def _on_close(self):
        if self.running:
            self._stop_run()
        self._save_config()
        self._close_ui()
        self.root.destroy()



class LabeledSlider:
    """与原版等效的滑块：数值徽标浮动在滑块当前值位置上方。"""

    def __init__(self, master, from_, to, number_of_steps=200, default=None,
                 unit="", **kwargs):
        import customtkinter as ctk

        self.unit = unit
        self.from_ = from_
        self.to = to
        self._value = default if default is not None else from_
        self._frame = ctk.CTkFrame(master, fg_color="transparent")

        self.slider = ctk.CTkSlider(
            self._frame, from_=from_, to=to, number_of_steps=number_of_steps,
            button_color=THEME_COLORS["accent"],
            button_hover_color=THEME_COLORS["accent_hover"],
            progress_color=THEME_COLORS["accent"], height=14, **kwargs)
        self.slider.pack(fill="x", expand=True)
        self.slider.set(self._value)

        self.badge = ctk.CTkLabel(
            self._frame, text=self._format(self._value),
            font=ctk.CTkFont(size=10, weight="bold", family="Consolas"),
            text_color=THEME_COLORS["bg"], fg_color=THEME_COLORS["accent"],
            corner_radius=4, padx=4, pady=0)
        self._bind_events()

    def _format(self, v):
        if self.unit in ("秒", "Mbps"):
            return f"{v:.1f}"
        return str(int(v))

    def _bind_events(self):
        self.slider.configure(command=self._on_change)
        self.slider.bind("<ButtonRelease-1>", self._on_release, add="+")

    def _on_change(self, v):
        self._value = v
        self.badge.configure(text=self._format(v))
        self._place_badge()

    def _on_release(self, _event=None):
        v = self.slider.get()
        self._value = v
        self.badge.configure(text=self._format(v))
        self._place_badge()

    def _place_badge(self):
        self._frame.after(5, self._do_place)

    def _do_place(self):
        w = self.slider.winfo_width()
        if w < 10:  # 尚未布局完成，稍后再试
            self._frame.after(20, self._do_place)
            return
        pad = 15
        track_w = w - 2 * pad
        span = self.to - self.from_
        ratio = (self._value - self.from_) / span if span else 0.0
        x = pad + int(ratio * track_w) - 6
        x = max(0, min(w - 10, x))
        self.badge.place(x=x, rely=0.5, anchor="w")

    def get(self):
        return self._value

    def set(self, v):
        self._value = v
        self.slider.set(v)
        self.badge.configure(text=self._format(v))
        self._place_badge()

    def pack(self, **kwargs):
        self._frame.pack(**kwargs)

    def configure(self, **kwargs):
        self._frame.configure(**kwargs)

    def destroy(self):
        self._frame.destroy()


def _cleanup_stale_mei_dirs(max_age_hours: float = 6.0) -> int:
    """清理 PyInstaller onefile 残留的解压目录（%TEMP%/_MEI*）。"""
    if not getattr(sys, "frozen", False):
        return 0
    try:
        current = getattr(sys, "_MEIPASS", None)
        tmp = Path(tempfile.gettempdir())
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
        return removed
    except Exception:
        return 0


_UA = {"User-Agent": "cf-ip-updater/1.0"}


def _get(url, timeout=6, decode="utf-8"):
    """GET 并解码；HTTPError 显式 close，避免响应临时文件泄漏。"""
    from urllib.error import HTTPError
    req = urllib.request.Request(url, headers=_UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode(decode, errors="replace")
    except HTTPError as exc:
        # HTTPError 携带未关闭的响应临时文件，GC 时触发 ResourceWarning
        try:
            exc.close()
        except Exception:
            pass
        raise


def query_my_ip():
    """多源探测本机公网 IP + 位置（国内优先，逐个 failover）。

    源顺序：百度 qifu(HTTPS) → IPIP.net(HTTPS) → 3322 → 太平洋 pconline(GBK) → ipify+ip-api。
    返回 (ip, location)；全部失败返回 None。
    """
    try:
        data = json.loads(_get("https://qifu-api.baidubce.com/ip/local/geo/v1/district"))
        d = data.get("data") or {}
        ip = d.get("ip")
        if ip:
            loc = d.get("location") or d.get("country") or ""
            return ip, loc
    except Exception:
        pass
    try:
        t = _get("https://myip.ipip.net")
        # 支持 IPv4 和 IPv6："当前 IP：xxx 来自于：位置"
        if "当前 IP：" in t:
            raw = t.split("当前 IP：", 1)[1]
            ip = raw.split()[0].strip() if raw.split() else ""
            loc = raw.split("来自于：", 1)[-1].strip() if "来自于：" in raw else ""
            if ip:
                return ip, loc
    except Exception:
        pass
    # 源3: 3322.dyndns 纯 IPv4 备选（国内极稳定，无位置但快）
    try:
        ip = _get("http://members.3322.org/dyndns/getip").strip()
        if ip and "." in ip:
            try:
                t2 = _get("https://myip.ipip.net")
                if "当前 IP：" in t2:
                    raw2 = t2.split("当前 IP：", 1)[1]
                    loc2 = raw2.split("来自于：", 1)[-1].strip() if "来自于：" in raw2 else ""
                    return ip, loc2
            except Exception:
                pass
            return ip, ""
    except Exception:
        pass
    try:
        t = _get("http://whois.pconline.com.cn/ipJson.jsp?json=true", decode="gbk")
        s = t.find("{")
        e = t.rfind("}")
        data = json.loads(t[s:e + 1]) if s != -1 else {}
        ip = data.get("ip")
        if ip:
            return ip, data.get("addr") or ""
    except Exception:
        pass
    try:
        ip = json.loads(_get("https://api.ipify.org?format=json"))["ip"]
        try:
            loc_data = json.loads(_get("http://ip-api.com/json/" + ip + "?lang=zh-CN"))
            loc = ""
            if loc_data.get("status") == "success":
                loc = " ".join(x for x in [loc_data.get("country", ""), loc_data.get("city", "")] if x)
            return ip, loc
        except Exception:
            return ip, ""
    except Exception:
        pass
    return None



def suggest_regions(location: str) -> str:
    """根据 IP 定位字符串推荐就近区域代码（逗号分隔，用于 --region）。

    映射规则：含"中国" → HK,JP,SG,KR；含"美国" → US；含"日本" → JP；
    含"韩国" → KR；含"新加坡" → SG；含"香港" → HK；含"台湾" → TW；
    含"欧洲/德国" → DE,FR,GB；含"英国" → GB；含"德国" → DE；含"法国" → FR。
    """
    if not location:
        return ""
    loc = location.lower()
    if "中国" in loc or "china" in loc:
        return "HK,JP,SG,KR"
    if "美国" in loc or "united states" in loc or "us" in loc.split():
        return "US"
    if "日本" in loc or "japan" in loc:
        return "JP"
    if "韩国" in loc or "korea" in loc:
        return "KR"
    if "新加坡" in loc or "singapore" in loc:
        return "SG"
    if "香港" in loc or "hong kong" in loc:
        return "HK"
    if "台湾" in loc or "taiwan" in loc:
        return "TW"
    if "欧洲" in loc or "europe" in loc or "germany" in loc:
        return "DE,FR,GB"
    if "英国" in loc or "united kingdom" in loc:
        return "GB"
    if "德国" in loc or "germany" in loc:
        return "DE"
    if "法国" in loc or "france" in loc:
        return "FR"
    return ""


def _fetch_my_ip(label_widget, ctk, region_callback=None, schedule=None):
    """后台线程获取本机公网 IP 和位置，用主线程调度更新 label。

    schedule 为可调用（App._ui）：线程安全入队，避免跨线程调 label.after 失效。
    """
    result = query_my_ip()
    if result is None:
        def _show():
            try:
                label_widget.configure(text="\U0001F4CD 本机 IP: 获取失败")
            except Exception:
                pass
        if schedule:
            schedule(_show)
        else:
            try:
                label_widget.after(0, _show)
            except Exception:
                pass
        return
    ip, location = result

    def _update():
        try:
            t = "\U0001F4CD 本机 IP: " + ip + (" \u00B7 " + location if location else "")
            label_widget.configure(text=t)
        except Exception:
            pass
        # 自动推荐区域（仅当用户尚未手动设置区域时）
        if region_callback and location:
            try:
                region_callback(location)
            except Exception:
                pass
    if schedule:
        schedule(_update)
    else:
        try:
            label_widget.after(0, _update)
        except Exception:
            pass


def main():
    import customtkinter as ctk

    _cleanup_stale_mei_dirs()
    if not acquire_single_instance():
        from tkinter import messagebox
        root = ctk.CTk()
        root.withdraw()
        messagebox.showwarning("CF 优选测速工具", "程序已经在运行，请使用已有窗口。", parent=root)
        root.destroy()
        return
    apply_theme(load_config().get("theme", "dark"))
    ctk.set_default_color_theme("dark-blue")
    root = ctk.CTk()
    # 设置窗口图标（打包后从 _MEIPASS 读取）
    try:
        icon_path = _resource_path() / "app_icon.png"
        if icon_path.exists():
            import tkinter as tk
            root.iconphoto(True, tk.PhotoImage(file=str(icon_path)))
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    # 源码直运行也切换到新版 Signal Desk；旧 App 类保留给兼容测试/辅助函数。
    from gui_redesign import main as redesign_main
    redesign_main()
