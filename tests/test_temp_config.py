"""Test per-run isolation without accessing pytest's potentially restricted default root."""

from pathlib import Path
from types import SimpleNamespace

from conftest import pytest_configure


def test_temp_directories_are_unique_and_bypass_default_root(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    configs = [SimpleNamespace(option=SimpleNamespace(basetemp=None)) for _ in range(2)]
    for config in configs:
        pytest_configure(config)
    paths = [config.option.basetemp for config in configs]
    assert paths[0] != paths[1]
    assert all(Path(path).parent == tmp_path for path in paths)


def test_explicit_basetemp_is_preserved():
    config = SimpleNamespace(option=SimpleNamespace(basetemp="custom-temp"))
    pytest_configure(config)
    assert config.option.basetemp == "custom-temp"
