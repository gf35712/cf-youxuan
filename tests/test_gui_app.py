# -*- coding: utf-8 -*-
"""GUI App 测试：真实 customtkinter 实例化 + 生命周期 + 线程安全调度。"""
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import gui  # noqa: E402

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


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "CONFIG_FILE", tmp_path / "gui_config.json")
    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    root.withdraw()
    a = gui.App(root)
    monkeypatch.setattr(a, "_load_results", lambda: None)
    yield a
    try:
        a._close_ui()
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass


class TestAppSmoke:
    def test_init_builds_ui(self, app):
        assert app.running is False
        assert hasattr(app, "_start_btn")
        assert hasattr(app, "_stop_btn")
        assert hasattr(app, "_progress_bar")
        assert hasattr(app, "_log_text")

    def test_sliders_have_defaults(self, app):
        params = {k: s.get() for k, s in app._sliders.items()}
        assert params["tcp_timeout"] == gui.DEFAULT_TCP_TIMEOUT
        assert params["min_speed"] == gui.DEFAULT_MIN_SPEED

    def test_collect_params(self, app):
        p = app._collect_params()
        assert p["sort"] == "speed"
        assert p["verify_top"] == gui.DEFAULT_VERIFY_TOP
        assert p["benchmark"] == gui.DEFAULT_BENCHMARK

    def test_ui_schedule_main_thread(self, app):
        """主线程调用 _ui 直接执行。"""
        calls = []
        app._ui(lambda: calls.append(1))
        assert calls == [1]

    def test_ui_schedule_worker_thread(self, app):
        """worker 线程调用 _ui 入队，主线程轮询执行。"""
        calls = []

        def worker():
            app._ui(lambda: calls.append(1))

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        # 驱动事件循环让 _poll_ui 执行
        deadline = time.time() + 3
        while not calls and time.time() < deadline:
            app.root.update()
            time.sleep(0.05)
        assert calls == [1]

    def test_progress_update(self, app):
        app._bar_ticking = False
        app._try_progress('{"progress": 0.8, "stage": "下载速度测试..."}')
        app.root.update()
        assert app._bar_target == pytest.approx(0.8)

    def test_save_config_roundtrip(self, app, tmp_path, monkeypatch):
        cfg_path = tmp_path / "cfg.json"
        monkeypatch.setattr(gui, "CONFIG_FILE", cfg_path)
        app._save_config()
        loaded = gui.load_config(cfg_path)
        assert loaded["tcp_workers"] == gui.DEFAULT_TCP_WORKERS

    def test_save_config_uses_live_controls(self, app, tmp_path, monkeypatch):
        """运行后用户调整滑块，保存时以控件值为准。"""
        cfg_path = tmp_path / "cfg.json"
        monkeypatch.setattr(gui, "CONFIG_FILE", cfg_path)
        app._cfg_snapshot = {"min_speed": 8.0}
        app._sliders["min_speed"].set(15.0)
        app._save_config()
        loaded = gui.load_config(cfg_path)
        assert loaded["min_speed"] == 15.0

    def test_restore_min_speed_v3_passthrough(self, app, monkeypatch):
        """config_version=3 的 min_speed（Mbps）直接恢复，不迁移。"""
        monkeypatch.setattr(app, "_cfg", {**app._cfg, "config_version": 3, "min_speed": 25})
        app._restore_values()
        assert abs(app._sliders["min_speed"].get() - 25.0) < 1e-6

    def test_stop_run_sets_state(self, app):
        app.running = True
        app.process = None
        app._stop_run()
        assert app.running is False

    def test_append_log_destroyed(self, app):
        """日志控件销毁后 append 不崩溃。"""
        app._log_insert("hello")
        assert "hello" in app._log_text.get("1.0", "end")

    def test_toggle_theme(self, app):
        old_theme = app._theme
        app._toggle_theme()
        assert app._theme != old_theme
        assert hasattr(app, "_start_btn")  # UI 重建成功

    def test_finish_run_ui(self, app):
        app._finish_run_ui(True)
        assert "完成" in app.status_label.cget("text")

    def test_start_run_missing_exe(self, app, tmp_path, monkeypatch):
        monkeypatch.setattr(gui, "resolve_update_exe", lambda p: None)
        app._work_dir_var.set(str(tmp_path))
        app._start_run()
        assert app.running is False