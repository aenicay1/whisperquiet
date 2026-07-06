import types

from whisperquiet import parakeet
from whisperquiet import transcribe as transcribe_mod


def test_whisper_unload_clears_matching_model_holder(monkeypatch):
    class Holder:
        model = object()
        model_path = "org/model"

    fake_module = types.SimpleNamespace(ModelHolder=Holder)
    gc_calls = []

    monkeypatch.setattr(
        transcribe_mod.importlib,
        "import_module",
        lambda name: fake_module if name == "mlx_whisper.transcribe" else None,
    )
    monkeypatch.setattr(transcribe_mod.gc, "collect", lambda: gc_calls.append(True))

    assert transcribe_mod.unload_model("org/model") is True
    assert Holder.model is None
    assert Holder.model_path is None
    assert gc_calls == [True]


def test_whisper_unload_ignores_different_model_repo(monkeypatch):
    model = object()

    class Holder:
        pass

    Holder.model = model
    Holder.model_path = "org/other"

    fake_module = types.SimpleNamespace(ModelHolder=Holder)

    monkeypatch.setattr(
        transcribe_mod.importlib,
        "import_module",
        lambda name: fake_module if name == "mlx_whisper.transcribe" else None,
    )

    assert transcribe_mod.unload_model("org/model") is False
    assert Holder.model is model
    assert Holder.model_path == "org/other"


def test_parakeet_unload_clears_requested_cached_model(monkeypatch):
    parakeet._MODELS.clear()
    parakeet._MODELS.update({"org/a": object(), "org/b": object()})
    gc_calls = []
    monkeypatch.setattr(parakeet.gc, "collect", lambda: gc_calls.append(True))

    try:
        assert parakeet.unload_model("org/a") is True
        assert "org/a" not in parakeet._MODELS
        assert "org/b" in parakeet._MODELS
        assert gc_calls == [True]
    finally:
        parakeet._MODELS.clear()
