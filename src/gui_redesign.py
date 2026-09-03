from __future__ import annotations

"""CF 优选全新界面：Signal Desk / 网络信号台。

本模块只重做桌面呈现层，测速引擎与 CLI 参数继续复用 gui.py/update.py，
保证旧配置、输出格式和停止/进度流程兼容。
"""

import re
from tkinter import ttk

import customtkinter as ctk

import gui as legacy
from gui import (
    COUNTRY_CN,
    DEFAULT_BENCHMARK,
    DEFAULT_IPS_URL,
    DEFAULT_MIN_SPEED,
    DEFAULT_SPEED_TIMEOUT,
    DEFAULT_SPEED_WORKERS,
    DEFAULT_GUI_SPEED_BYTES,
    DEFAULT_GUI_FAST_MODE,
    DEFAULT_GUI_STOP_AFTER_FAST, DEFAULT_GUI_RECHECK_TOP, DEFAULT_GUI_RECHECK_SAMPLES,
    DEFAULT_GUI_MAX_PACKET_LOSS,
    CANDIDATE_MODE_LABELS, CANDIDATE_MODE_KEYS, DEFAULT_GUI_CANDIDATE_MODE, DEFAULT_GUI_CANDIDATE_LIMIT,
    DEFAULT_TCP_TIMEOUT,
    DEFAULT_TCP_WORKERS,
    DEFAULT_TOP,
    DEFAULT_VERIFY_TOP,
    SOURCE_KEYS,
    SOURCE_LABELS,
    SCAN_KEYS,
    SCAN_LABELS,
    SCAN_DESCRIPTIONS,
    SPEED_SOURCE_LABELS,
    speed_source_preset,
    speed_source_url,
    _safe_float,
    _safe_int,
    filter_pick_rows,
    load_config,
    parse_curl_speed_result,
    parse_progress_line,
    progress_stage,
    region_label,
    save_config,
    sort_pick_rows,
    summarize_results,
    suggest_regions,
)

APP_NAME = "CF 优选 · Signal Desk"

PALETTE = {
    "dark": {
        "bg": "#0B1220", "panel": "#111C2E", "panel_alt": "#16253B",
        "panel_deep": "#09101C", "line": "#263A55", "text": "#F4F7FB",
        "muted": "#91A4BE", "soft": "#B7C4D6", "accent": "#55D6BE",
        "accent_hover": "#3BBBA5", "amber": "#FFB86B", "danger": "#FF7A90",
        "blue": "#7AA2FF", "good": "#79E2A8",
    },
    "light": {
        "bg": "#EEF2F6", "panel": "#FFFFFF", "panel_alt": "#F5F8FB",
        "panel_deep": "#E5ECF3", "line": "#D4DEE9", "text": "#172235",
        "muted": "#68758A", "soft": "#526176", "accent": "#0E8878",
        "accent_hover": "#086C60", "amber": "#C86F2A", "danger": "#C94860",
        "blue": "#4A6FCC", "good": "#16834B",
    },
}


class SignalSlider:
    """紧凑的参数滑块，数值显示和标题放在同一行。"""

    def __init__(self, master, title: str, from_: float, to: float,
                 default: float, unit: str = "", steps: int = 100):
        self.unit = unit
        self.from_ = from_
        self.to = to
        self._value = default
        self.frame = ctk.CTkFrame(master, fg_color="transparent")
        top = ctk.CTkFrame(self.frame, fg_color="transparent")
        top.pack(fill="x", pady=(0, 2))
        color = master._signal_color if hasattr(master, "_signal_color") else lambda key: "gray"
        ctk.CTkLabel(top, text=title, anchor="w", font=ctk.CTkFont(size=11),
                     text_color=color("soft")).pack(side="left")
        self.badge = ctk.CTkLabel(
            top, text=self._format(default), width=70,
            font=ctk.CTkFont(size=11, weight="bold", family="Cascadia Mono"),
            text_color=color("bg"), fg_color=color("accent"), corner_radius=4)
        self.badge.pack(side="right")
        self.slider = ctk.CTkSlider(
            self.frame, from_=from_, to=to, number_of_steps=steps, height=12,
            progress_color=color("accent"), button_color=color("accent"),
            button_hover_color=color("accent_hover"))
        self.slider.pack(fill="x")
        self.slider.set(default)
        self.slider.configure(command=self._on_change)

    def _format(self, value: float) -> str:
        if self.unit in ("s", "Mbps"):
            return f"{value:.1f} {self.unit}" if self.unit else f"{value:.1f}"
        return f"{int(round(value))} {self.unit}" if self.unit else str(int(round(value)))

    def _on_change(self, value):
        self._value = float(value)
        self.badge.configure(text=self._format(self._value))

    def get(self):
        return self._value

    def set(self, value):
        self._value = float(value)
        self.slider.set(value)
        self.badge.configure(text=self._format(self._value))

    def pack(self, **kwargs):
        self.frame.pack(**kwargs)

    def grid(self, **kwargs):
        self.frame.grid(**kwargs)

    def destroy(self):
        self.frame.destroy()


class App(legacy.App):
    """Signal Desk 新版 UI；继承旧流程控制，重写全部界面构建。"""

    def _signal_color(self, key: str) -> str:
        return PALETTE.get(self._theme, PALETTE["dark"])[key]

    def _card(self, parent, title: str, eyebrow: str = ""):
        card = ctk.CTkFrame(parent, fg_color=self._signal_color("panel"),
                            border_width=1, border_color=self._signal_color("line"),
                            corner_radius=14)
        if eyebrow:
            ctk.CTkLabel(card, text=eyebrow.upper(), anchor="w",
                         font=ctk.CTkFont(size=9, weight="bold"),
                         text_color=self._signal_color("accent")).pack(
                             fill="x", padx=16, pady=(13, 2))
        ctk.CTkLabel(card, text=title, anchor="w",
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=self._signal_color("text")).pack(
                         fill="x", padx=16, pady=(0, 10))
        return card

    def _entry(self, parent, variable=None, placeholder="", mono=False, **kwargs):
        return ctk.CTkEntry(
            parent, textvariable=variable, placeholder_text=placeholder,
            height=34, corner_radius=8, border_width=1,
            border_color=self._signal_color("line"),
            fg_color=self._signal_color("panel_deep"),
            text_color=self._signal_color("text"),
            placeholder_text_color=self._signal_color("muted"),
            font=ctk.CTkFont(size=11, family="Cascadia Mono" if mono else "Microsoft YaHei UI"),
            **kwargs)

    def _button(self, parent, text, command, kind="ghost", width=None, **kwargs):
        if kind == "primary":
            fg, hover, txt, border = (self._signal_color("accent"), self._signal_color("accent_hover"),
                                      self._signal_color("bg"), self._signal_color("accent"))
        elif kind == "danger":
            fg, hover, txt, border = (self._signal_color("danger"), self._signal_color("danger"),
                                      self._signal_color("bg"), self._signal_color("danger"))
        else:
            fg, hover, txt, border = (self._signal_color("panel_alt"), self._signal_color("line"),
                                      self._signal_color("soft"), self._signal_color("line"))
        return ctk.CTkButton(
            parent, text=text, command=command, height=34, width=width or 100,
            corner_radius=8, border_width=1, fg_color=fg, hover_color=hover,
            text_color=txt, border_color=border,
            font=ctk.CTkFont(size=11, weight="bold"), **kwargs)

    def _build_ui(self):
        ctk = self.ctk
        self._adv_expanded = getattr(self, "_adv_expanded", False)
        self._pick_region_code = getattr(self, "_pick_region_code", "")
        self._pick_rows = getattr(self, "_pick_rows", [])
        self._pick_selected = getattr(self, "_pick_selected", set())
        self._pick_sort_col = getattr(self, "_pick_sort_col", "latency")
        self._pick_sort_rev = getattr(self, "_pick_sort_rev", False)

        self.root.title(APP_NAME)
        self.root.geometry("1320x860")
        self.root.minsize(1180, 760)
        self.root.configure(fg_color=self._signal_color("bg"))
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._ips_url_var = ctk.StringVar(value=self._cfg.get("ips_url") or DEFAULT_IPS_URL)
        self._source_var = ctk.StringVar(value=SOURCE_KEYS.get(self._cfg.get("source_type", "url"), "在线列表"))
        self._local_file_var = ctk.StringVar(value=str(self._cfg.get("local_file", "")))
        self._scan_var = ctk.StringVar(value=SCAN_KEYS.get(self._cfg.get("scan_mode", "tcping"), "TCPing (握手)"))
        self._annotate_var = ctk.BooleanVar(value=bool(self._cfg.get("annotate", True)))
        self._work_dir_var = ctk.StringVar(value=str(self.work_dir))
        self._update_exe_var = ctk.StringVar(value=str(self._cfg.get("update_exe", "")))
        self._verbose_var = ctk.BooleanVar(value=bool(self._cfg.get("verbose", True)))
        self._sort_var = ctk.StringVar(value=legacy.SORT_KEYS.get(self._cfg.get("sort", "speed"), "按速度"))
        self._csv_var = ctk.BooleanVar(value=bool(self._cfg.get("csv_export", False)))
        self._benchmark_var = ctk.BooleanVar(value=bool(self._cfg.get("benchmark_enabled", True)))
        # GUI 使用 1MiB 快速筛选；CLI 默认仍保留 4MiB。
        self._speed_bytes_var = ctk.StringVar(value=str(self._cfg.get("speed_bytes", DEFAULT_GUI_SPEED_BYTES)))
        self._fast_mode_var = ctk.BooleanVar(value=bool(self._cfg.get("fast_mode", DEFAULT_GUI_FAST_MODE)))
        self._candidate_mode_var = ctk.StringVar(value=CANDIDATE_MODE_KEYS.get(
            self._cfg.get("candidate_mode", DEFAULT_GUI_CANDIDATE_MODE), "自适应"))
        self._candidate_limit_var = ctk.StringVar(value=str(
            self._cfg.get("candidate_limit", DEFAULT_GUI_CANDIDATE_LIMIT)))
        self._recheck_top_var = ctk.StringVar(value=str(
            self._cfg.get("recheck_top", DEFAULT_GUI_RECHECK_TOP)))
        self._recheck_samples_var = ctk.StringVar(value=str(
            self._cfg.get("recheck_samples", DEFAULT_GUI_RECHECK_SAMPLES)))
        self._max_packet_loss_var = ctk.StringVar(value=str(
            self._cfg.get("max_packet_loss", DEFAULT_GUI_MAX_PACKET_LOSS)))
        self._metric_vars = {key: ctk.StringVar(value="—") for key in ("tested", "best", "latency", "speed")}
        self._stage_var = ctk.StringVar(value="等待开始扫描")

        outer = ctk.CTkFrame(self.root, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=22, pady=18)
        outer.grid_rowconfigure(1, weight=1)
        # 左侧是高频控制台，给足宽度避免参数标题、路径和策略说明被截断。
        outer.grid_columnconfigure(0, weight=0, minsize=420)
        outer.grid_columnconfigure(1, weight=1, minsize=700)

        self._build_header(outer)
        self._build_controls(outer)
        self._build_dashboard(outer)
        self._build_statusbar(outer)

    def _build_header(self, parent):
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 16))
        header.grid_columnconfigure(1, weight=1)
        mark = ctk.CTkFrame(header, width=42, height=42, corner_radius=12, fg_color=self._signal_color("accent"))
        mark.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, 12))
        mark.grid_propagate(False)
        ctk.CTkLabel(mark, text="CF", text_color=self._signal_color("bg"),
                     font=ctk.CTkFont(size=16, weight="bold", family="Cascadia Mono")).place(relx=.5, rely=.5, anchor="center")
        ctk.CTkLabel(header, text="CF 优选", anchor="w", font=ctk.CTkFont(size=26, weight="bold"),
                     text_color=self._signal_color("text")).grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(header, text="Signal Desk  ·  找到当前网络真正快的节点", anchor="w",
                     font=ctk.CTkFont(size=11), text_color=self._signal_color("muted")).grid(row=1, column=1, sticky="w", pady=(1, 0))
        self._ip_bar = ctk.CTkLabel(header, text="● 本机网络：获取中…", cursor="hand2", anchor="e",
                                    font=ctk.CTkFont(size=10), text_color=self._signal_color("muted"))
        self._ip_bar.grid(row=0, column=2, sticky="e", padx=(12, 14))
        self._ip_bar.bind("<Button-1>", lambda _event: self._refresh_ip())
        self._header_status = ctk.CTkLabel(header, text="● READY", anchor="e",
                                           font=ctk.CTkFont(size=10, weight="bold", family="Cascadia Mono"),
                                           text_color=self._signal_color("accent"))
        self._header_status.grid(row=1, column=2, sticky="e", padx=(12, 14))
        self._theme_btn = ctk.CTkButton(header, text="☾", width=34, height=34, corner_radius=9,
                                        fg_color=self._signal_color("panel_alt"), hover_color=self._signal_color("line"),
                                        text_color=self._signal_color("text"), border_width=1,
                                        border_color=self._signal_color("line"), command=self._toggle_theme)
        self._theme_btn.grid(row=0, column=3, rowspan=2, sticky="e")

    def _build_controls(self, parent):
        self._controls = ctk.CTkScrollableFrame(
            parent, label_text="控制台 / CONTROL DECK", label_font=ctk.CTkFont(size=11, weight="bold"),
            label_text_color=self._signal_color("muted"), fg_color=self._signal_color("panel"),
            corner_radius=14, border_width=1, border_color=self._signal_color("line"))
        self._controls.grid(row=1, column=0, sticky="nsew", padx=(0, 16))

        source = self._card(self._controls, "从哪里找节点", "01 · 数据源")
        source.pack(fill="x", padx=10, pady=(8, 6))
        source_menu = ctk.CTkOptionMenu(
            source, variable=self._source_var, values=list(SOURCE_LABELS), height=32, corner_radius=8,
            fg_color=self._signal_color("panel_alt"), button_color=self._signal_color("panel_alt"),
            button_hover_color=self._signal_color("line"), text_color=self._signal_color("text"),
            font=ctk.CTkFont(size=11), command=lambda _value: self._rebuild_source_widgets())
        source_menu.pack(fill="x", padx=16, pady=(0, 8))
        self._source_menu = source_menu
        self._src_frame = ctk.CTkFrame(source, fg_color="transparent")
        self._src_frame.pack(fill="x", padx=16, pady=(0, 14))
        self._rebuild_source_widgets()

        scan = self._card(self._controls, "怎么测", "02 · 扫描策略")
        scan.pack(fill="x", padx=10, pady=6)
        scan_body = ctk.CTkFrame(scan, fg_color="transparent")
        scan_body.pack(fill="x", padx=16, pady=(0, 14))
        self._scan_menu = ctk.CTkOptionMenu(
            scan_body, variable=self._scan_var, values=list(SCAN_LABELS), height=32, corner_radius=8,
            fg_color=self._signal_color("panel_alt"), button_color=self._signal_color("panel_alt"),
            button_hover_color=self._signal_color("line"), text_color=self._signal_color("text"),
            font=ctk.CTkFont(size=11))
        self._scan_menu.pack(fill="x", pady=(0, 8))
        self._scan_hint = ctk.CTkLabel(
            scan_body, text=SCAN_DESCRIPTIONS.get(self._cfg.get("scan_mode", "tcping"), SCAN_DESCRIPTIONS["tcping"]),
            wraplength=380, justify="left", anchor="w", text_color=self._signal_color("muted"),
            font=ctk.CTkFont(size=9))
        self._scan_hint.pack(fill="x", pady=(0, 8))
        self._scan_menu.configure(command=self._on_scan_mode_change)
        ctk.CTkSwitch(scan_body, text="标注节点地区（只标注，不自动筛选）", variable=self._annotate_var,
                      onvalue=True, offvalue=False, progress_color=self._signal_color("accent"),
                      button_color=self._signal_color("text"), button_hover_color=self._signal_color("soft"),
                      text_color=self._signal_color("soft"), font=ctk.CTkFont(size=11)).pack(anchor="w")

        tuning = self._card(self._controls, "把速度和并发调到合适", "03 · 参数")
        tuning.pack(fill="x", padx=10, pady=6)
        tune_body = ctk.CTkFrame(tuning, fg_color="transparent")
        tune_body.pack(fill="x", padx=16, pady=(0, 12))
        strategy_row = ctk.CTkFrame(tune_body, fg_color="transparent")
        strategy_row.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(strategy_row, text="下载候选策略", anchor="w", text_color=self._signal_color("soft"),
                     font=ctk.CTkFont(size=10)).pack(side="left")
        self._candidate_mode_menu = ctk.CTkOptionMenu(
            strategy_row, variable=self._candidate_mode_var, values=list(CANDIDATE_MODE_LABELS),
            width=116, height=28, corner_radius=8, fg_color=self._signal_color("panel_alt"),
            button_color=self._signal_color("panel_alt"), button_hover_color=self._signal_color("line"),
            text_color=self._signal_color("text"), font=ctk.CTkFont(size=10))
        self._candidate_mode_menu.pack(side="right")
        self._sliders = {}
        # SignalSlider 需要从宿主 App 读取当前主题色；CTkFrame 本身没有该方法。
        tune_body._signal_color = self._signal_color
        specs = [
            ("tcp_timeout", "延迟超时", 1.0, 5.0, DEFAULT_TCP_TIMEOUT, "s", 80),
            ("tcp_workers", "延迟并发", 20, 400, DEFAULT_TCP_WORKERS, "", 76),
            ("speed_timeout", "测速超时", 2, 15, DEFAULT_SPEED_TIMEOUT, "s", 65),
            ("speed_workers", "测速并发", 1, 24, DEFAULT_SPEED_WORKERS, "", 23),
            ("min_speed", "优选阈值", 0, 100, DEFAULT_MIN_SPEED, "Mbps", 100),
            ("top_per_region", "每区候选", 1, 50, DEFAULT_TOP, "", 49),
        ]
        for key, title, lo, hi, default, unit, steps in specs:
            slider = SignalSlider(tune_body, title, lo, hi, self._params.get(key, default), unit, steps)
            slider.pack(fill="x", pady=(0, 9))
            self._sliders[key] = slider
        ctk.CTkSwitch(
            tune_body, text="快速采样模式  ·  1MiB / 16并发（可选早停）", variable=self._fast_mode_var,
            onvalue=True, offvalue=False, progress_color=self._signal_color("accent"),
            button_color=self._signal_color("text"), button_hover_color=self._signal_color("soft"),
            text_color=self._signal_color("accent"), font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", pady=(0, 5))
        ctk.CTkLabel(
            tune_body, text="TCP 每次扫描全部输入节点；默认完整测速候选池，快速模式可手动启用早停。",
            wraplength=440, justify="left", anchor="w", text_color=self._signal_color("muted"),
            font=ctk.CTkFont(size=9)).pack(fill="x", pady=(0, 2))

        self._adv_btn = ctk.CTkButton(
            self._controls, text="▸ 高级设置", height=32, anchor="w", fg_color="transparent",
            hover_color=self._signal_color("panel_alt"), text_color=self._signal_color("muted"),
            font=ctk.CTkFont(size=11, weight="bold"), command=self._toggle_adv)
        self._adv_btn.pack(fill="x", padx=16, pady=(4, 0))
        self._build_advanced()

        action = ctk.CTkFrame(self._controls, fg_color="transparent")
        action.pack(fill="x", padx=10, pady=(12, 14))
        self._start_btn = self._button(action, "开始扫描  ↗", self._start_run, "primary", width=150)
        self._start_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._stop_btn = self._button(action, "停止", self._stop_run, "danger", width=80)
        self._stop_btn.pack(side="right")
        self._stop_btn.configure(state="disabled")

    def _build_advanced(self):
        """创建独立高级设置面板：双栏布局，不再挤在左侧窄栏中。"""
        ctk = self.ctk
        self._adv_window = ctk.CTkToplevel(self.root)
        self._adv_window.title("高级设置 · CF 优选")
        self._adv_window.geometry("900x800")
        self._adv_window.minsize(840, 740)
        self._adv_window.configure(fg_color=self._signal_color("bg"))
        self._adv_window.protocol("WM_DELETE_WINDOW", self._close_adv)
        self._adv_window.withdraw()

        self._adv_frame = ctk.CTkFrame(
            self._adv_window, fg_color=self._signal_color("panel"), corner_radius=16,
            border_width=1, border_color=self._signal_color("line"))
        self._adv_frame.pack(fill="both", expand=True, padx=16, pady=16)
        self._adv_frame.grid_rowconfigure(1, weight=1)
        self._adv_frame.grid_columnconfigure(0, weight=1)
        self._adv_frame.grid_columnconfigure(1, weight=1)

        header = ctk.CTkFrame(self._adv_frame, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=18, pady=(16, 10))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="高级设置", anchor="w",
                     font=ctk.CTkFont(size=19, weight="bold"),
                     text_color=self._signal_color("text")).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(header, text="只在需要时打开；常用扫描参数留在左侧控制台。",
                     anchor="w", font=ctk.CTkFont(size=10),
                     text_color=self._signal_color("muted")).grid(row=1, column=0, sticky="w", pady=(2, 0))
        self._button(header, "关闭", self._close_adv, "ghost", width=68).grid(row=0, column=1, rowspan=2, sticky="e")

        left = ctk.CTkFrame(self._adv_frame, fg_color=self._signal_color("panel_alt"), corner_radius=12)
        left.grid(row=1, column=0, sticky="nsew", padx=(18, 7), pady=(0, 14))
        right = ctk.CTkFrame(self._adv_frame, fg_color=self._signal_color("panel_alt"), corner_radius=12)
        right.grid(row=1, column=1, sticky="nsew", padx=(7, 18), pady=(0, 14))
        for card in (left, right):
            card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(left, text="筛选与测速", anchor="w", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=self._signal_color("accent")).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(14, 8))
        self._region_entry = self._entry(left, placeholder="例如 HK,SG", mono=True)
        self._proxy_entry = self._entry(left, placeholder="列表代理（失败自动直连）", mono=True)
        self._speed_port_entry = self._entry(left, placeholder="0 = 节点端口", mono=True)
        self._samples_entry = self._entry(left, placeholder="1 - 8", mono=True)
        self._speed_bytes_entry = self._entry(left, variable=self._speed_bytes_var, placeholder="默认 1048576", mono=True)
        self._candidate_limit_entry = self._entry(left, variable=self._candidate_limit_var, placeholder="默认 300", mono=True)
        source_row = ctk.CTkFrame(left, fg_color="transparent")
        self._speed_url_entry = self._entry(source_row, placeholder="自定义测速地址", mono=True)
        self._setting_speed_source = False
        self._speed_source_var = ctk.StringVar(
            value=speed_source_preset(self._cfg.get("speed_url", "")))
        self._speed_source_menu = ctk.CTkOptionMenu(
            source_row, variable=self._speed_source_var, values=list(SPEED_SOURCE_LABELS),
            width=110, height=30, corner_radius=8,
            fg_color=self._signal_color("panel_alt"),
            button_color=self._signal_color("panel_alt"),
            button_hover_color=self._signal_color("line"),
            text_color=self._signal_color("text"), font=ctk.CTkFont(size=10),
            command=self._on_speed_source_change)
        self._speed_source_menu.pack(side="left", padx=(0, 6))
        self._speed_url_entry.pack(side="left", fill="x", expand=True)
        for row, label, widget in [
            (1, "地区过滤", self._region_entry), (2, "测速源", source_row),
            (3, "列表代理", self._proxy_entry), (4, "测速端口", self._speed_port_entry),
            (5, "测速采样", self._samples_entry), (6, "测速字节", self._speed_bytes_entry),
            (7, "候选上限", self._candidate_limit_entry),
        ]:
            ctk.CTkLabel(left, text=label, width=62, anchor="w", text_color=self._signal_color("muted"),
                         font=ctk.CTkFont(size=10)).grid(row=row, column=0, sticky="w", padx=(14, 6), pady=5)
            widget.grid(row=row, column=1, sticky="ew", padx=(0, 14), pady=5)
        self._speed_url_entry.bind("<KeyRelease>", self._on_speed_url_edited)

        ctk.CTkLabel(right, text="运行与导出", anchor="w", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=self._signal_color("accent")).grid(row=0, column=0, columnspan=3, sticky="w", padx=14, pady=(14, 8))
        self._export_entry = self._entry(right, placeholder="csv v2ray clash singbox", mono=True)
        self._benchmark_entry = self._entry(right, placeholder=DEFAULT_BENCHMARK, mono=True)
        self._verify_entry = self._entry(right, placeholder="0 = 不复核", mono=True)
        self._interval_entry = self._entry(right, placeholder="0 = 不定时重跑", mono=True)
        self._speed_proxy_entry = self._entry(right, placeholder="测速代理（留空 = 直连）", mono=True)
        self._recheck_top_entry = self._entry(right, variable=self._recheck_top_var, placeholder="0 = 不复测", mono=True)
        self._recheck_samples_entry = self._entry(right, variable=self._recheck_samples_var, placeholder="默认 3", mono=True)
        self._max_packet_loss_entry = self._entry(right, variable=self._max_packet_loss_var, placeholder="0 - 100", mono=True)
        for row, label, widget in [
            (1, "导出格式", self._export_entry), (2, "基准节点", self._benchmark_entry),
            (3, "复核数量", self._verify_entry), (4, "重跑间隔", self._interval_entry),
            (5, "测速代理", self._speed_proxy_entry),
            (6, "复测前 K", self._recheck_top_entry), (7, "复测次数", self._recheck_samples_entry),
            (8, "最大丢包率", self._max_packet_loss_entry),
        ]:
            ctk.CTkLabel(right, text=label, width=62, anchor="w", text_color=self._signal_color("muted"),
                         font=ctk.CTkFont(size=10)).grid(row=row, column=0, sticky="w", padx=(14, 6), pady=5)
            widget.grid(row=row, column=1, columnspan=2, sticky="ew", padx=(0, 14), pady=5)
        ctk.CTkCheckBox(right, text="运行基准测试", variable=self._benchmark_var,
                        text_color=self._signal_color("soft"), fg_color=self._signal_color("accent"),
                        hover_color=self._signal_color("accent_hover"), font=ctk.CTkFont(size=10)).grid(
                            row=9, column=1, sticky="w", padx=(0, 14), pady=(9, 5))
        self._work_dir_entry = self._entry(right, variable=self._work_dir_var, placeholder="工作目录", mono=True)
        self._update_exe_entry = self._entry(right, variable=self._update_exe_var, placeholder="自动查找 update.exe", mono=True)
        for row, label, widget, cmd in [
            (10, "工作目录", self._work_dir_entry, self._browse_work_dir),
            (11, "CLI 程序", self._update_exe_entry, self._browse_update_exe),
        ]:
            ctk.CTkLabel(right, text=label, width=62, anchor="w", text_color=self._signal_color("muted"),
                         font=ctk.CTkFont(size=10)).grid(row=row, column=0, sticky="w", padx=(14, 6), pady=5)
            widget.grid(row=row, column=1, sticky="ew", padx=(0, 6), pady=5)
            self._button(right, "选择", cmd, "ghost", width=54).grid(row=row, column=2, padx=(0, 14), pady=5)

        footer = ctk.CTkFrame(self._adv_frame, fg_color="transparent")
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", padx=18, pady=(0, 14))
        ctk.CTkLabel(footer, text="列表代理失败会自动直连；测速代理留空 = 节点直连测速。",
                     text_color=self._signal_color("muted"), font=ctk.CTkFont(size=10)).pack(side="left")
        self._button(footer, "保存设置", self._save_advanced, "primary", width=112).pack(side="right")

    def _close_adv(self):
        win = getattr(self, "_adv_window", None)
        if win is None:
            return
        try:
            win.grab_release()
        except Exception:
            pass
        try:
            win.withdraw()
        except Exception:
            pass

    def _toggle_adv(self):
        win = getattr(self, "_adv_window", None)
        if win is None:
            return
        try:
            if win.state() == "normal":
                self._close_adv()
                return
            win.update_idletasks()
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            ww, wh = 900, 800
            win.geometry(f"{ww}x{wh}+{max(0, (sw-ww)//2)}+{max(0, (sh-wh)//2)}")
            win.deiconify()
            win.lift()
            win.focus_force()
            win.grab_set()
        except Exception:
            pass

    def _save_advanced(self):
        self._save_config()
        self._close_adv()
        self._flash_status("高级设置已保存")


    def _rebuild_source_widgets(self):
        for child in self._src_frame.winfo_children():
            child.destroy()
        src = SOURCE_LABELS.get(self._source_var.get(), "url")
        if src == "file":
            row = ctk.CTkFrame(self._src_frame, fg_color="transparent")
            row.pack(fill="x")
            self._local_file_entry = self._entry(row, variable=self._local_file_var, placeholder="选择 txt / csv 文件", mono=True)
            self._local_file_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
            self._button(row, "浏览", self._browse_input_file, "ghost", width=58).pack(side="right")
        elif src in ("official4", "official6"):
            ip_txt = "IPv4" if src == "official4" else "IPv6"
            ctk.CTkLabel(self._src_frame, text=f"使用 Cloudflare 官方 {ip_txt} 段，每段随机采样候选 IP。",
                         wraplength=440, justify="left", anchor="w", text_color=self._signal_color("muted"),
                         font=ctk.CTkFont(size=10)).pack(fill="x")
        else:
            self._ips_url_entry = self._entry(self._src_frame, variable=self._ips_url_var,
                                               placeholder="在线 IP 列表地址", mono=True)
            self._ips_url_entry.pack(fill="x")

    def _build_dashboard(self, parent):
        dashboard = ctk.CTkFrame(parent, fg_color="transparent")
        dashboard.grid(row=1, column=1, sticky="nsew")
        # 结果工作区占主要高度；指标摘要放在内容区底部，避免抢占首屏空间。
        dashboard.grid_rowconfigure(1, weight=1)
        dashboard.grid_columnconfigure(0, weight=1)

        hero = ctk.CTkFrame(dashboard, fg_color=self._signal_color("panel"), corner_radius=14,
                           border_width=1, border_color=self._signal_color("line"))
        hero.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        hero.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(hero, text="扫描控制室", anchor="w", font=ctk.CTkFont(size=18, weight="bold"),
                     text_color=self._signal_color("text")).grid(row=0, column=0, sticky="w", padx=18, pady=(15, 2))
        ctk.CTkLabel(hero, text="先看可达性，再看速度；所有结果都留在你的工作目录。", anchor="w",
                     font=ctk.CTkFont(size=10), text_color=self._signal_color("muted")).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 12))
        self._live_label = ctk.CTkLabel(hero, text="等待任务 · 选择左侧参数后开始扫描", anchor="w",
                                        font=ctk.CTkFont(size=11, family="Cascadia Mono"), text_color=self._signal_color("accent"))
        self._live_label.grid(row=0, column=1, rowspan=2, sticky="e", padx=18)

        metrics = ctk.CTkFrame(dashboard, fg_color="transparent")
        metrics.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        self._metrics_frame = metrics
        for col in range(4):
            metrics.grid_columnconfigure(col, weight=1)
        metric_defs = [("tested", "测速节点", "NODE"), ("best", "优选结果", "FAST"),
                       ("latency", "最低延迟", "LATENCY"), ("speed", "最高速度", "SPEED")]
        for col, (key, label, eyebrow) in enumerate(metric_defs):
            card = ctk.CTkFrame(metrics, fg_color=self._signal_color("panel"), corner_radius=12,
                                border_width=1, border_color=self._signal_color("line"))
            card.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 5, 5 if col < 3 else 0))
            ctk.CTkLabel(card, text=eyebrow, anchor="w", font=ctk.CTkFont(size=9, weight="bold"),
                         text_color=self._signal_color("muted")).pack(fill="x", padx=13, pady=(11, 1))
            ctk.CTkLabel(card, textvariable=self._metric_vars[key], anchor="w",
                         font=ctk.CTkFont(size=21, weight="bold", family="Cascadia Mono"),
                         text_color=self._signal_color("accent" if key in ("best", "speed") else "text")).pack(fill="x", padx=13, pady=(0, 2))
            ctk.CTkLabel(card, text=label, anchor="w", font=ctk.CTkFont(size=10),
                         text_color=self._signal_color("soft")).pack(fill="x", padx=13, pady=(0, 10))

        self._build_results_panel(dashboard)

        progress = ctk.CTkFrame(dashboard, fg_color=self._signal_color("panel"), corner_radius=12,
                                border_width=1, border_color=self._signal_color("line"))
        progress.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        progress.grid_columnconfigure(0, weight=1)
        self._progress_label = ctk.CTkLabel(progress, textvariable=self._stage_var, anchor="w",
                                            font=ctk.CTkFont(size=10), text_color=self._signal_color("muted"))
        self._progress_label.grid(row=0, column=0, sticky="w", padx=14, pady=(10, 3))
        self._progress_bar = ctk.CTkProgressBar(progress, height=8, corner_radius=4,
                                               fg_color=self._signal_color("panel_deep"), progress_color=self._signal_color("accent"))
        self._progress_bar.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 11))
        self._progress_bar.set(0)

    def _build_results_panel(self, parent):
        tabs = ctk.CTkTabview(parent, fg_color=self._signal_color("panel"), corner_radius=14,
                              border_width=1, border_color=self._signal_color("line"),
                              segmented_button_fg_color=self._signal_color("panel_alt"),
                              segmented_button_selected_color=self._signal_color("accent"),
                              segmented_button_selected_hover_color=self._signal_color("accent_hover"),
                              text_color=self._signal_color("text"))
        tabs.grid(row=1, column=0, sticky="nsew")
        self._tabs = tabs
        try:
            tabs._segmented_button.configure(font=ctk.CTkFont(size=11, weight="bold"))
        except Exception:
            pass

        best_tab = tabs.add("优选结果")
        best_tab.grid_rowconfigure(0, weight=1)
        best_tab.grid_columnconfigure(0, weight=1)
        self._best_text = ctk.CTkTextbox(best_tab, fg_color=self._signal_color("panel_deep"),
                                         text_color=self._signal_color("good"), font=ctk.CTkFont(size=12, family="Cascadia Mono"),
                                         corner_radius=10, wrap="none")
        self._best_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self._button(best_tab, "复制优选结果", self._copy_output, "ghost", width=120).grid(row=1, column=0, sticky="e", padx=8, pady=(0, 8))

        full_tab = tabs.add("全部结果")
        full_tab.grid_rowconfigure(0, weight=1)
        full_tab.grid_columnconfigure(0, weight=1)
        self._full_text = ctk.CTkTextbox(full_tab, fg_color=self._signal_color("panel_deep"),
                                         text_color=self._signal_color("soft"), font=ctk.CTkFont(size=11, family="Cascadia Mono"),
                                         corner_radius=10, wrap="none")
        self._full_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self._button(full_tab, "复制全部结果", lambda: self._copy_output(self._full_text), "ghost", width=120).grid(row=1, column=0, sticky="e", padx=8, pady=(0, 8))

        pick_tab = tabs.add("筛选候选")
        pick_tab.grid_rowconfigure(1, weight=1)
        pick_tab.grid_columnconfigure(0, weight=1)
        filters = ctk.CTkFrame(pick_tab, fg_color="transparent")
        filters.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        filters.grid_columnconfigure(4, weight=1)
        self._pick_region_btn = self._button(filters, "全部地区", self._toggle_region_menu, "ghost", width=110)
        self._pick_region_btn.grid(row=0, column=0, padx=(0, 6))
        self._pick_maxlat_entry = self._entry(filters, placeholder="延迟 ≤ ms", mono=True, width=90)
        self._pick_maxlat_entry.grid(row=0, column=1, padx=3)
        self._pick_minspeed_entry = self._entry(filters, placeholder="速度 ≥ Mbps", mono=True, width=100)
        self._pick_minspeed_entry.grid(row=0, column=2, padx=3)
        self._pick_fast_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(filters, text="仅优选", variable=self._pick_fast_var, command=self._apply_filter,
                        text_color=self._signal_color("soft"), fg_color=self._signal_color("accent"),
                        hover_color=self._signal_color("accent_hover"), font=ctk.CTkFont(size=10)).grid(row=0, column=3, padx=(7, 4))
        self._pick_select_all_btn = self._button(
            filters, "全选当前", self._pick_select_all, "ghost", width=82)
        self._pick_select_all_btn.grid(row=0, column=4, padx=(4, 4))
        self._button(filters, "清除", self._pick_filter_reset, "ghost", width=58).grid(row=0, column=5, sticky="e")
        self._pick_maxlat_entry.bind("<KeyRelease>", self._on_pick_filter_key)
        self._pick_minspeed_entry.bind("<KeyRelease>", self._on_pick_filter_key)

        table = ctk.CTkFrame(pick_tab, fg_color=self._signal_color("panel_deep"), corner_radius=10)
        table.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        table.grid_rowconfigure(0, weight=1)
        table.grid_columnconfigure(0, weight=1)
        self._pick_tree = ttk.Treeview(table, style="CFRedesign.Treeview",
                                       columns=("node", "region", "latency", "speed", "fast"), show="tree headings", selectmode="none")
        self._pick_tree.heading("#0", text="选中")
        self._pick_tree.heading("node", text="节点", command=lambda: self._pick_sort_toggle("node"))
        self._pick_tree.heading("region", text="地区", command=lambda: self._pick_sort_toggle("region"))
        self._pick_tree.heading("latency", text="延迟", command=lambda: self._pick_sort_toggle("latency"))
        self._pick_tree.heading("speed", text="速度", command=lambda: self._pick_sort_toggle("speed"))
        self._pick_tree.heading("fast", text="状态")
        self._pick_tree.column("#0", width=55, anchor="center", stretch=False)
        self._pick_tree.column("node", width=210, anchor="w")
        self._pick_tree.column("region", width=100, anchor="w")
        self._pick_tree.column("latency", width=90, anchor="e")
        self._pick_tree.column("speed", width=100, anchor="e")
        self._pick_tree.column("fast", width=80, anchor="center", stretch=False)
        self._pick_tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self._pick_tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self._pick_tree.configure(yscrollcommand=scroll.set)
        self._pick_tree.bind("<Button-1>", self._on_pick_click)
        footer = ctk.CTkFrame(pick_tab, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=8, pady=(4, 8))
        self._button(footer, "复制所选", self._copy_picked, "primary", width=100).pack(side="left", padx=(0, 6))
        self._button(footer, "导出所选", self._export_picked, "ghost", width=100).pack(side="left")
        ctk.CTkLabel(footer, text="点击行切换 · √ 表示已选 · 列头可排序", text_color=self._signal_color("muted"),
                     font=ctk.CTkFont(size=10)).pack(side="right")

        log_tab = tabs.add("运行日志")
        log_tab.grid_rowconfigure(0, weight=1)
        log_tab.grid_columnconfigure(0, weight=1)
        self._log_text = ctk.CTkTextbox(log_tab, fg_color=self._signal_color("panel_deep"),
                                        text_color=self._signal_color("soft"), font=ctk.CTkFont(size=11, family="Cascadia Mono"),
                                        corner_radius=10, wrap="word")
        self._log_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self._button(log_tab, "清空日志", lambda: self._log_text.delete("0.0", "end"), "ghost", width=90).grid(row=1, column=0, sticky="e", padx=8, pady=(0, 8))
        self._apply_tree_style()

    def _build_statusbar(self, parent):
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        bar.grid_columnconfigure(1, weight=1)
        self.status_label = ctk.CTkLabel(bar, text="就绪 · 等待扫描", text_color=self._signal_color("muted"),
                                         font=ctk.CTkFont(size=10, weight="bold"))
        self.status_label.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(bar, text="输出目录：", text_color=self._signal_color("muted"), font=ctk.CTkFont(size=10)).grid(row=0, column=1, sticky="e")
        self._output_hint = ctk.CTkLabel(bar, textvariable=self._work_dir_var, anchor="e",
                                         text_color=self._signal_color("soft"), font=ctk.CTkFont(size=10, family="Cascadia Mono"))
        self._output_hint.grid(row=0, column=2, sticky="e", padx=(4, 0))

    def _apply_tree_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("CFRedesign.Treeview", background=self._signal_color("panel_deep"),
                        foreground=self._signal_color("soft"), fieldbackground=self._signal_color("panel_deep"),
                        rowheight=30, borderwidth=0, font=("Cascadia Mono", 10))
        style.configure("CFRedesign.Treeview.Heading", background=self._signal_color("panel_alt"),
                        foreground=self._signal_color("text"), borderwidth=0,
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.map("CFRedesign.Treeview", background=[("selected", self._signal_color("panel_deep"))],
                  foreground=[("selected", self._signal_color("soft"))])
        try:
            self._pick_tree.tag_configure("fast", foreground=self._signal_color("good"))
        except Exception:
            pass

    def _close_ui(self):
        # 高级设置是独立 Toplevel，根窗口销毁前也要显式关闭，避免残留窗口/抓取焦点。
        win = getattr(self, "_adv_window", None)
        if win is not None:
            try:
                win.grab_release()
            except Exception:
                pass
            try:
                win.destroy()
            except Exception:
                pass
            self._adv_window = None
        super()._close_ui()

    def _toggle_theme(self):
        if self.running:
            self._flash_status("扫描进行中，完成后再切换主题")
            return
        self._theme = "light" if self._theme == "dark" else "dark"
        legacy.apply_theme(self._theme)
        self._close_ui()
        for child in self.root.winfo_children():
            child.destroy()
        self._build_ui()
        self._restore_values()
        self._apply_tree_style()
        self._save_config()

    def _restore_values(self):
        super()._restore_values()
        self._speed_proxy_entry.delete(0, "end")
        self._speed_proxy_entry.insert(0, self._cfg.get("speed_proxy", ""))
        self._speed_source_var.set(speed_source_preset(self._cfg.get("speed_url", "")))
        self._speed_bytes_var.set(str(self._cfg.get("speed_bytes", DEFAULT_GUI_SPEED_BYTES)))
        self._candidate_mode_var.set(CANDIDATE_MODE_KEYS.get(
            self._cfg.get("candidate_mode", DEFAULT_GUI_CANDIDATE_MODE), "自适应"))
        self._candidate_limit_var.set(str(self._cfg.get("candidate_limit", DEFAULT_GUI_CANDIDATE_LIMIT)))
        self._recheck_top_var.set(str(self._cfg.get("recheck_top", DEFAULT_GUI_RECHECK_TOP)))
        self._recheck_samples_var.set(str(self._cfg.get("recheck_samples", DEFAULT_GUI_RECHECK_SAMPLES)))
        self._max_packet_loss_var.set(str(self._cfg.get("max_packet_loss", DEFAULT_GUI_MAX_PACKET_LOSS)))

    def _on_speed_source_change(self, label):
        """选择预设时填入地址；切到自定义时保留现有输入。"""
        if label in SPEED_SOURCE_LABELS:
            self._speed_source_var.set(label)
        value = speed_source_url(label)
        if value is None:
            return
        self._setting_speed_source = True
        try:
            self._speed_url_entry.delete(0, "end")
            if value:
                self._speed_url_entry.insert(0, value)
        finally:
            self._setting_speed_source = False

    def _on_speed_url_edited(self, _event=None):
        """用户手动编辑地址后，将下拉状态切换为自定义。"""
        if not self._setting_speed_source and self._speed_source_var.get() != "自定义":
            self._speed_source_var.set("自定义")

    def _toggle_pick(self, iid):
        """切换一行选择，只更新第一列勾选标记，不改变整行颜色。"""
        selected = getattr(self, "_pick_selected", set())
        if iid in selected:
            selected.discard(iid)
            marker = ""
        else:
            selected.add(iid)
            marker = "√"
        try:
            item = self._pick_tree.item(iid)
            tags = tuple(tag for tag in item.get("tags", ()) if tag != "sel")
            self._pick_tree.item(iid, text=marker, tags=tags)
        except Exception:
            pass
        self._update_pick_select_all_button()

    def _pick_select_all(self):
        """全选或取消全选当前筛选后可见的行。"""
        visible = list(self._pick_tree.get_children())
        selected = getattr(self, "_pick_selected", set())
        select = bool(visible) and not all(iid in selected for iid in visible)
        for iid in visible:
            if select:
                selected.add(iid)
            else:
                selected.discard(iid)
            try:
                item = self._pick_tree.item(iid)
                tags = tuple(tag for tag in item.get("tags", ()) if tag != "sel")
                self._pick_tree.item(iid, text="√" if select else "", tags=tags)
            except Exception:
                pass
        self._update_pick_select_all_button()

    def _pick_clear_all(self):
        """清空所有选择。"""
        getattr(self, "_pick_selected", set()).clear()
        for iid in self._pick_tree.get_children():
            try:
                item = self._pick_tree.item(iid)
                tags = tuple(tag for tag in item.get("tags", ()) if tag != "sel")
                self._pick_tree.item(iid, text="", tags=tags)
            except Exception:
                pass
        self._update_pick_select_all_button()

    def _update_pick_select_all_button(self):
        """根据当前可见行的选择状态更新按钮文案。"""
        try:
            visible = list(self._pick_tree.get_children())
            selected = getattr(self, "_pick_selected", set())
            label = "取消全选" if visible and all(iid in selected for iid in visible) else "全选当前"
            self._pick_select_all_btn.configure(text=label)
        except Exception:
            pass

    def _apply_filter(self):
        """复用旧筛选逻辑，但将选择表现收敛为第一列勾选。"""
        super()._apply_filter()
        selected = getattr(self, "_pick_selected", set())
        try:
            for iid in self._pick_tree.get_children():
                item = self._pick_tree.item(iid)
                tags = tuple(tag for tag in item.get("tags", ()) if tag != "sel")
                self._pick_tree.item(iid, text="√" if iid in selected else "", tags=tags)
        except Exception:
            pass
        self._update_pick_select_all_button()

    def _on_scan_mode_change(self, label):
        """切换扫描模式时同步显示延迟数据的含义。"""
        mode = SCAN_LABELS.get(label, "tcping")
        self._scan_hint.configure(text=SCAN_DESCRIPTIONS.get(mode, SCAN_DESCRIPTIONS["tcping"]))

    def _collect_params(self) -> dict:
        params = super()._collect_params()
        speed_bytes = _safe_int(self._speed_bytes_var.get(), DEFAULT_GUI_SPEED_BYTES)
        params["speed_bytes"] = max(64 * 1024, min(speed_bytes, 16 * 1024 * 1024))
        if self._fast_mode_var.get():
            params["speed_bytes"] = DEFAULT_GUI_SPEED_BYTES
            params["speed_workers"] = max(int(params["speed_workers"]), 16)
        params["fast_mode"] = bool(self._fast_mode_var.get())
        params["stop_after_fast"] = DEFAULT_GUI_STOP_AFTER_FAST if self._fast_mode_var.get() else 0
        params["recheck_top"] = max(0, min(1000, _safe_int(
            self._recheck_top_var.get(), DEFAULT_GUI_RECHECK_TOP)))
        params["recheck_samples"] = max(1, min(8, _safe_int(
            self._recheck_samples_var.get(), DEFAULT_GUI_RECHECK_SAMPLES)))
        params["max_packet_loss"] = max(0.0, min(100.0, _safe_float(
            self._max_packet_loss_var.get(), DEFAULT_GUI_MAX_PACKET_LOSS)))
        params["candidate_mode"] = CANDIDATE_MODE_LABELS.get(
            self._candidate_mode_var.get(), DEFAULT_GUI_CANDIDATE_MODE)
        params["candidate_limit"] = max(10, min(1000, _safe_int(
            self._candidate_limit_var.get(), DEFAULT_GUI_CANDIDATE_LIMIT)))
        params["benchmark"] = self._benchmark_entry.get().strip() or DEFAULT_BENCHMARK
        params["benchmark_enabled"] = bool(self._benchmark_var.get())
        params["verify_top"] = _safe_int(self._verify_entry.get(), DEFAULT_VERIFY_TOP)
        params["speed_proxy"] = self._speed_proxy_entry.get().strip()
        return params

    def _save_config(self):
        super()._save_config()
        try:
            data = legacy.load_config(legacy.CONFIG_FILE)
            data.update({
                "benchmark": self._benchmark_entry.get().strip() or DEFAULT_BENCHMARK,
                "benchmark_enabled": bool(self._benchmark_var.get()),
                "verify_top": _safe_int(self._verify_entry.get(), DEFAULT_VERIFY_TOP),
                "speed_bytes": max(64 * 1024, min(_safe_int(self._speed_bytes_var.get(), DEFAULT_GUI_SPEED_BYTES), 16 * 1024 * 1024)),
                "fast_mode": bool(self._fast_mode_var.get()),
                "stop_after_fast": DEFAULT_GUI_STOP_AFTER_FAST if self._fast_mode_var.get() else 0,
                "recheck_top": max(0, min(1000, _safe_int(
                    self._recheck_top_var.get(), DEFAULT_GUI_RECHECK_TOP))),
                "recheck_samples": max(1, min(8, _safe_int(
                    self._recheck_samples_var.get(), DEFAULT_GUI_RECHECK_SAMPLES))),
                "max_packet_loss": max(0.0, min(100.0, _safe_float(
                    self._max_packet_loss_var.get(), DEFAULT_GUI_MAX_PACKET_LOSS))),
                "candidate_mode": CANDIDATE_MODE_LABELS.get(
                    self._candidate_mode_var.get(), DEFAULT_GUI_CANDIDATE_MODE),
                "candidate_limit": max(10, min(1000, _safe_int(
                    self._candidate_limit_var.get(), DEFAULT_GUI_CANDIDATE_LIMIT))),
                "speed_proxy": self._speed_proxy_entry.get().strip(),
                "theme": self._theme,
            })
            legacy.save_config(data, legacy.CONFIG_FILE)
        except Exception:
            pass

    def _update_live(self, line):
        super()._update_live(line)
        m = re.search(r"\[(LAT|SPEED)\] (\S+) -> ([\d.]+) (ms|Mbps)", line)
        if not m:
            return
        _kind, node, value, unit = m.groups()
        try:
            self._ui(lambda: self._live_label.configure(text=f"{node}  ·  {value}{unit}"))
        except Exception:
            pass

    def _finish_run_ui(self, ok, **details):
        super()._finish_run_ui(ok, **details)
        self._refresh_metrics()
        try:
            self._header_status.configure(text="● READY" if ok else "● CHECK LOG",
                                          text_color=self._signal_color("accent" if ok else "danger"))
        except Exception:
            pass

    def _refresh_metrics(self):
        try:
            full = self._full_text.get("0.0", "end")
            best = self._best_text.get("0.0", "end")
            fast, total, lat, speed = summarize_results(best)
            self._metric_vars["tested"].set(str(total if total else len(full.splitlines()) if full.strip() else 0))
            self._metric_vars["best"].set(str(fast))
            self._metric_vars["latency"].set(f"{lat:.1f} ms" if lat is not None else "—")
            self._metric_vars["speed"].set(f"{speed:.2f} Mbps" if speed is not None else "—")
        except Exception:
            pass

    def _do_update_bar(self, v, t, tested=0, total=0):
        super()._do_update_bar(v, t, tested, total)
        try:
            self._stage_var.set(f"{t}{f'  ·  {tested}/{total}' if total else ''}")
        except Exception:
            pass


def main():
    if not legacy.acquire_single_instance():
        from tkinter import messagebox
        root = ctk.CTk()
        root.withdraw()
        messagebox.showwarning("CF 优选测速工具", "程序已经在运行，请使用已有窗口。", parent=root)
        root.destroy()
        return
    root = ctk.CTk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()

