import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cache  # noqa: E402


def test_repo_from_huggingface_cache_dir_name():
    assert (
        cache._repo_from_cache_dir("models--mlx-community--whisper-large-v3-turbo")
        == "mlx-community/whisper-large-v3-turbo"
    )
    assert cache._repo_from_cache_dir("other") == "other"


def test_render_status_lists_app_data_and_model_caches(tmp_path):
    config_dir = tmp_path / "config"
    audio_dir = config_dir / "audio"
    audio_dir.mkdir(parents=True)
    (audio_dir / "take.wav").write_bytes(b"1" * 2048)

    model_dir = (
        tmp_path
        / "hf"
        / "hub"
        / "models--mlx-community--whisper-large-v3-turbo"
    )
    model_dir.mkdir(parents=True)
    (model_dir / "weights.bin").write_bytes(b"1" * 1024)

    out = cache.render_status(config_dir=config_dir, hf_home=tmp_path / "hf")

    assert "app data:" in out
    assert "audio" in out
    assert "huggingface cache:" in out
    assert "shipping" in out
    assert "mlx-community/whisper-large-v3-turbo" in out
