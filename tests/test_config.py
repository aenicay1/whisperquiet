"""Tests for atomic config persistence and the new Phase-0 flags.

CONFIG_DIR / CONFIG_PATH are module globals pointing at the real home dir, so
every test redirects them at the config module to a tmp dir — the real config is
never touched.
"""
import json
import os

import pytest

from whisperquiet import config as config_mod
from whisperquiet.config import Config


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.json")
    return tmp_path


def test_save_load_roundtrip(tmp_config):
    cfg = Config(ptt_key="f13", vad_gate_enabled=True, vocabulary=["Springdale"])
    config_mod.save(cfg)
    back = config_mod.load()
    assert back.ptt_key == "f13"
    assert back.vad_gate_enabled is True
    assert back.vocabulary == ["Springdale"]


def test_new_flag_defaults_off():
    assert Config().vad_gate_enabled is False


def test_save_leaves_no_temp_file(tmp_config):
    config_mod.save(Config())
    leftovers = list(tmp_config.glob(".config-*"))
    assert leftovers == []
    assert (tmp_config / "config.json").exists()


def test_save_overwrites_atomically(tmp_config):
    config_mod.save(Config(ptt_key="alt_r"))
    config_mod.save(Config(ptt_key="f14"))
    data = json.loads((tmp_config / "config.json").read_text())
    assert data["ptt_key"] == "f14"
    assert list(tmp_config.glob(".config-*")) == []  # no temp residue


def test_failed_replace_cleans_temp_and_preserves_old(tmp_config, monkeypatch):
    config_mod.save(Config(ptt_key="alt_r"))  # establish a good file

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(config_mod.os, "replace", boom)
    with pytest.raises(OSError):
        config_mod.save(Config(ptt_key="f15"))
    # temp cleaned up, and the original file is intact (atomicity guarantee)
    assert list(tmp_config.glob(".config-*")) == []
    assert json.loads((tmp_config / "config.json").read_text())["ptt_key"] == "alt_r"


def test_load_ignores_unknown_keys(tmp_config):
    (tmp_config / "config.json").write_text(json.dumps({"ptt_key": "f13", "bogus": 1}))
    cfg = config_mod.load()
    assert cfg.ptt_key == "f13"
    assert not hasattr(cfg, "bogus")
