# -*- coding: utf-8 -*-
"""输入源模块契约测试，避免回归到真实网络。"""
import io
import json
import sys
from pathlib import Path
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import input_sources  # noqa: E402


def test_merge_nodes_supports_injected_downloader(tmp_path):
    def fake_download(url, path, timeout, **kwargs):
        content = "1.1.1.1:443#US\n2.2.2.2:443#JP\n" if url == "one" else "2.2.2.2:443#JP\n3.3.3.3:443#SG\n"
        Path(path).write_text(content, encoding="utf-8")
        return True

    out = input_sources.merge_nodes_from_urls(
        ["one", "two"], tmp_path / "ips.txt", download_fn=fake_download)
    assert [node.ip for node in out] == ["1.1.1.1", "2.2.2.2", "3.3.3.3"]
    assert len((tmp_path / "ips.txt").read_text(encoding="utf-8").splitlines()) == 3


def test_generate_official_nodes_supports_injected_prefix_fetcher():
    def fake_fetch(iptype, timeout):
        assert iptype == 4
        return ("192.0.2.0/29",)

    nodes = input_sources.generate_official_nodes(4, per_prefix=2, fetch_fn=fake_fetch)
    assert len(nodes) == 2
    assert all(node.port == 443 and node.region == "" for node in nodes)
    assert all(node.ip.startswith("192.0.2.") for node in nodes)


def test_refresh_input_http_error_closes_response(monkeypatch, tmp_path):
    bodies = []

    def fake_urlopen(*args, **kwargs):
        body = io.BytesIO(b"bad")
        bodies.append(body)
        raise HTTPError("https://source.test/", 530, "origin error", {}, body)

    class FakeOpener:
        def open(self, *args, **kwargs):
            return fake_urlopen(*args, **kwargs)

    monkeypatch.setattr(input_sources.urllib.request, "build_opener", lambda *args: FakeOpener())
    monkeypatch.setattr(input_sources, "_download_with_curl", lambda *args, **kwargs: False)
    assert input_sources.refresh_input_file("https://source.test/", tmp_path / "ips.txt", 0.1) is False
    assert bodies and all(body.closed for body in bodies)


def test_refresh_input_with_proxy_falls_back_to_direct(monkeypatch, tmp_path, capsys):
    calls = []
    dest = tmp_path / "ips.txt"

    def fake_refresh(url, path, timeout, *, skip_existing=False, proxy=""):
        calls.append(proxy)
        if proxy:
            return False
        Path(path).write_text("1.1.1.1:443#US\n", encoding="utf-8")
        return True

    monkeypatch.setattr(input_sources, "refresh_input_file", fake_refresh)
    assert input_sources.refresh_input_file_with_fallback(
        "https://source.test/", dest, 1, proxy="http://127.0.0.1:7890") is True
    assert calls == ["http://127.0.0.1:7890", ""]
    assert "retrying input list directly" in capsys.readouterr().out
    assert dest.exists()


def test_input_constants_are_valid():
    assert input_sources.MAX_INPUT_DOWNLOAD_BYTES > 0
    assert input_sources.DEFAULT_INPUT_URLS
    assert input_sources.OFFICIAL_PREFIXES_V4_URL.startswith("https://")
