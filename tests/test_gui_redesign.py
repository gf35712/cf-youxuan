# -*- coding: utf-8 -*-
"""新版 Signal Desk UI 的最小契约测试。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import gui as legacy  # noqa: E402
import gui_redesign as redesign  # noqa: E402

try:
    import customtkinter as ctk
except ImportError:
    ctk = None


def _can_open_gui():
    if ctk is None:
        return False
    try:
        root = ctk.CTk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _can_open_gui(), reason="无可用 GUI 会话")


def test_signal_palette_is_distinctive():
    assert redesign.PALETTE["dark"]["accent"] == "#55D6BE"
    assert redesign.PALETTE["dark"]["panel"] != legacy.DARK_THEME["card"]
    assert legacy.APP_NAME == "CF优选测速工具 · Signal Desk"


def test_redesign_app_builds_and_rethemes(tmp_path, monkeypatch):
    monkeypatch.setattr(legacy, "CONFIG_FILE", tmp_path / "gui_config.json")
    monkeypatch.setattr(legacy, "_fetch_my_ip", lambda *args, **kwargs: None)
    root = ctk.CTk()
    root.withdraw()
    app = redesign.App(root)
    try:
        assert app.root.title() == redesign.APP_NAME
        # 主布局给左侧控制台和结果区设置明确最小宽度，避免字段/说明被截断。
        assert (app.root._min_width, app.root._min_height) == (1180, 760)
        assert len(app._sliders) == 6
        params = app._collect_params()
        assert params["speed_bytes"] == redesign.DEFAULT_GUI_SPEED_BYTES
        assert params["speed_workers"] >= 16
        assert params["candidate_mode"] == "adaptive"
        assert params["candidate_limit"] == redesign.DEFAULT_GUI_CANDIDATE_LIMIT
        assert params["stop_after_fast"] == redesign.DEFAULT_GUI_STOP_AFTER_FAST
        assert params["recheck_top"] == redesign.DEFAULT_GUI_RECHECK_TOP
        assert params["recheck_samples"] == redesign.DEFAULT_GUI_RECHECK_SAMPLES
        assert params["max_packet_loss"] == redesign.DEFAULT_GUI_MAX_PACKET_LOSS
        assert hasattr(app, "_pick_tree")
        assert app._speed_source_var.get() == "自动选择"
        assert hasattr(app, "_scan_hint")
        assert "TCP 握手" in app._scan_hint.cget("text")
        assert app._tabs.grid_info()["row"] == 1
        assert app._metrics_frame.grid_info()["row"] == 3
        assert (app._adv_window._min_width, app._adv_window._min_height) == (840, 740)
        app._toggle_theme()
        assert app._theme == "light"
    finally:
        app._close_ui()
        root.destroy()
def test_redesign_controls_keep_source_and_proxy_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(legacy, "CONFIG_FILE", tmp_path / "gui_config.json")
    monkeypatch.setattr(legacy, "_fetch_my_ip", lambda *args, **kwargs: None)
    root = ctk.CTk()
    root.withdraw()
    app = redesign.App(root)
    try:
        app._source_var.set("本地文件")
        app._local_file_var.set(str(tmp_path / "ips.txt"))
        app._rebuild_source_widgets()
        app._toggle_adv()
        app._proxy_entry.insert(0, "http://127.0.0.1:7890")
        params = app._collect_params()
        assert params["source_type"] == "file"
        assert params["local_file"].endswith("ips.txt")
        assert params["proxy"] == "http://127.0.0.1:7890"
        assert params["speed_proxy"] == ""
        app._speed_proxy_entry.insert(0, "http://127.0.0.1:7891")
        assert app._collect_params()["speed_proxy"] == "http://127.0.0.1:7891"
        app._on_speed_source_change("CM提供")
        assert app._speed_url_entry.get() == "https://cf.090227.xyz/__down"
        assert app._speed_source_var.get() == "CM提供"
    finally:
        app._close_ui()
        root.destroy()
def test_advanced_settings_use_separate_window(tmp_path, monkeypatch):
    monkeypatch.setattr(legacy, "CONFIG_FILE", tmp_path / "gui_config.json")
    monkeypatch.setattr(legacy, "_fetch_my_ip", lambda *args, **kwargs: None)
    root = ctk.CTk()
    root.withdraw()
    app = redesign.App(root)
    try:
        assert app._adv_window.state() == "withdrawn"
        app._toggle_adv()
        app._adv_window.update_idletasks()
        assert app._adv_window.state() == "normal"
        app._toggle_adv()
        assert app._adv_window.state() == "withdrawn"
    finally:
        app._close_ui()
        root.destroy()


def test_pick_select_all_is_toggle_and_only_uses_check_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(legacy, "CONFIG_FILE", tmp_path / "gui_config.json")
    monkeypatch.setattr(legacy, "_fetch_my_ip", lambda *args, **kwargs: None)
    root = ctk.CTk()
    root.withdraw()
    app = redesign.App(root)
    try:
        app._pick_tree.insert("", "end", iid="a", text="", tags=(), values=("1.1.1.1:443", "HK", "10.0", "20.0", "✓"))
        app._pick_tree.insert("", "end", iid="b", text="", tags=(), values=("2.2.2.2:443", "JP", "20.0", "5.0", ""))
        app._pick_select_all()
        assert app._pick_selected == {"a", "b"}
        assert all(app._pick_tree.item(iid, "text") == "√" for iid in ("a", "b"))
        assert all("sel" not in app._pick_tree.item(iid, "tags") for iid in ("a", "b"))
        assert app._pick_select_all_btn.cget("text") == "取消全选"
        app._pick_select_all()
        assert app._pick_selected == set()
        assert all(app._pick_tree.item(iid, "text") == "" for iid in ("a", "b"))
    finally:
        app._close_ui()
        root.destroy()

