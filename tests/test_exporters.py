# -*- coding: utf-8 -*-
"""导出模块契约测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import exporters  # noqa: E402
import update  # noqa: E402
from models import Node, SpeedResult  # noqa: E402


def _results():
    return [
        SpeedResult(Node("1.1.1.1", 443, "US"), 20.0, 50.0, True),
        SpeedResult(Node("2.2.2.2", 443, "JP"), 10.0, 80.0, False),
    ]


def test_filtered_sorted_respects_region_and_sort():
    out = exporters._filtered_sorted(_results(), "latency", ("us",))
    assert [item.node.ip for item in out] == ["1.1.1.1"]


def test_write_results_is_atomic_and_formats_fast_label(tmp_path):
    path = tmp_path / "best.txt"
    exporters.write_results(path, _results())
    text = path.read_text(encoding="utf-8")
    assert "优选高速" in text
    assert not path.with_name(path.name + ".tmp").exists()


def test_clash_yaml_escapes_name(tmp_path):
    path = tmp_path / "clash.yaml"
    result = SpeedResult(Node("192.0.2.1", 443, 'A"B'), 1.0, 2.0, False)
    exporters.export_clash(path, [result], name_prefix="X\\Y")
    text = path.read_text(encoding="utf-8")
    assert 'name: "X\\\\Y-A\\"B-2M"' in text


def test_update_reexports_exporters():
    assert update.write_results is exporters.write_results
    assert update.export_csv is exporters.export_csv
    assert update.export_v2ray is exporters.export_v2ray
    assert update.export_singbox is exporters.export_singbox
    assert update.export_clash is exporters.export_clash
