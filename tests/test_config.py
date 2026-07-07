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
    assert Config().config_version == config_mod.CONFIG_VERSION
    assert Config().model_repo == config_mod.ACCURACY_MODEL_REPO
    assert Config().model_profile == config_mod.DEFAULT_MODEL_PROFILE
    assert Config().vad_gate_enabled is False
    assert Config().mlx_cache_limit_mb == 256
    assert Config().mlx_memory_limit_mb == 0
    assert Config().mlx_clear_cache_after_decode is True
    assert Config().model_idle_unload_s == 45.0


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


def test_load_migrates_legacy_default_model_to_accuracy_profile(tmp_config):
    (tmp_config / "config.json").write_text(
        json.dumps({"model_repo": config_mod.ACCURACY_MODEL_REPO})
    )

    cfg = config_mod.load()

    assert cfg.model_profile == "accuracy"
    assert cfg.model_repo == config_mod.ACCURACY_MODEL_REPO
    assert cfg.config_version == config_mod.CONFIG_VERSION


def test_load_migrates_old_light_default_to_accuracy_profile(tmp_config):
    (tmp_config / "config.json").write_text(
        json.dumps(
            {
                "model_profile": "light",
                "model_repo": config_mod.LIGHT_MODEL_REPO,
            }
        )
    )

    cfg = config_mod.load()

    assert cfg.model_profile == "accuracy"
    assert cfg.model_repo == config_mod.ACCURACY_MODEL_REPO
    assert cfg.config_version == config_mod.CONFIG_VERSION


def test_load_preserves_versioned_explicit_light_profile(tmp_config):
    (tmp_config / "config.json").write_text(
        json.dumps(
            {
                "config_version": config_mod.CONFIG_VERSION,
                "model_profile": "light",
                "model_repo": config_mod.LIGHT_MODEL_REPO,
            }
        )
    )

    cfg = config_mod.load()

    assert cfg.model_profile == "light"
    assert cfg.model_repo == config_mod.LIGHT_MODEL_REPO
    assert cfg.config_version == config_mod.CONFIG_VERSION


def test_load_preserves_explicit_accuracy_profile(tmp_config):
    (tmp_config / "config.json").write_text(
        json.dumps(
            {
                "model_profile": "accuracy",
                "model_repo": config_mod.ACCURACY_MODEL_REPO,
            }
        )
    )

    cfg = config_mod.load()

    assert cfg.model_profile == "accuracy"
    assert cfg.model_repo == config_mod.ACCURACY_MODEL_REPO
    assert cfg.config_version == config_mod.CONFIG_VERSION


def test_load_marks_unknown_legacy_repo_as_custom(tmp_config):
    (tmp_config / "config.json").write_text(json.dumps({"model_repo": "org/custom"}))

    cfg = config_mod.load()

    assert cfg.model_profile == "custom"
    assert cfg.model_repo == "org/custom"
    assert cfg.config_version == config_mod.CONFIG_VERSION
