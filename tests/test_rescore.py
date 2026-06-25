import sys

import pytest

from whisperquiet import rescore as rescore_mod
from whisperquiet.rescore import RescoreConfig, rescore


def test_disabled_is_identity_and_never_imports_mlx_lm(monkeypatch):
    # The _generate seam must never be reached on the disabled path, and the
    # optional dependency must never be imported.
    def boom(*args, **kwargs):
        raise AssertionError("_generate must not run when disabled")

    monkeypatch.setattr(rescore_mod, "_generate", boom)
    monkeypatch.delitem(sys.modules, "mlx_lm", raising=False)

    text = "purchase agreements to the Warriors"
    assert rescore(text) == text  # default config is disabled
    assert rescore(text, RescoreConfig(enabled=False)) == text
    assert "mlx_lm" not in sys.modules


def test_empty_and_blank_return_unchanged_without_generate(monkeypatch):
    monkeypatch.setattr(
        rescore_mod,
        "_generate",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not run")),
    )
    cfg = RescoreConfig(enabled=True)
    assert rescore("", cfg) == ""
    assert rescore("   ", cfg) == "   "
    assert rescore("\n\t", cfg) == "\n\t"


def test_correction_is_applied(monkeypatch):
    monkeypatch.setattr(
        rescore_mod, "_generate", lambda text, cfg: "purchase agreements to the lawyers"
    )
    out = rescore("purchase agreements to the Warriors", RescoreConfig(enabled=True))
    assert out == "purchase agreements to the lawyers"


def test_model_output_quotes_are_stripped(monkeypatch):
    monkeypatch.setattr(rescore_mod, "_generate", lambda text, cfg: '"camera permissions and setup"')
    out = rescore("camera for missions and set up", RescoreConfig(enabled=True))
    assert out == "camera permissions and setup"


def test_exception_returns_original(monkeypatch):
    def boom(text, cfg):
        raise RuntimeError("model load failed")

    monkeypatch.setattr(rescore_mod, "_generate", boom)
    text = "letter from Zed"
    assert rescore(text, RescoreConfig(enabled=True)) == text


def test_missing_mlx_lm_returns_original(monkeypatch):
    # Exercise the REAL _generate with no mlx_lm installed: its lazy import must
    # raise ImportError, which rescore() swallows, returning the original.
    import builtins

    real_import = builtins.__import__

    def blocking_import(name, *args, **kwargs):
        if name == "mlx_lm" or name.startswith("mlx_lm."):
            raise ImportError("No module named 'mlx_lm'")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "mlx_lm", raising=False)
    monkeypatch.setattr(builtins, "__import__", blocking_import)

    text = "letter from Zed"
    assert rescore(text, RescoreConfig(enabled=True)) == text


def test_runaway_rewrite_returns_original(monkeypatch):
    text = "send it to the lawyers"
    runaway = (text + " ") * 3  # 3x longer: a paraphrase/hallucination, not a fix
    monkeypatch.setattr(rescore_mod, "_generate", lambda t, c: runaway)
    assert rescore(text, RescoreConfig(enabled=True)) == text


def test_empty_model_output_returns_original(monkeypatch):
    text = "letter of intent"
    monkeypatch.setattr(rescore_mod, "_generate", lambda t, c: "")
    assert rescore(text, RescoreConfig(enabled=True)) == text
    monkeypatch.setattr(rescore_mod, "_generate", lambda t, c: "   ")
    assert rescore(text, RescoreConfig(enabled=True)) == text


def test_refusal_preamble_returns_original(monkeypatch):
    text = "letter from Zed"
    for refusal in (
        "Sure! Here's the corrected text: letter of intent",
        "Here is the corrected text: letter of intent",
        "I'm sorry, I can't help with that.",
        "As an AI language model, I cannot edit text.",
    ):
        monkeypatch.setattr(rescore_mod, "_generate", lambda t, c, r=refusal: r)
        assert rescore(text, RescoreConfig(enabled=True)) == text


def test_latency_overrun_returns_original(monkeypatch):
    # A generate that "takes" longer than the budget by advancing the clock.
    ticks = iter([0.0, 5.0])  # 5s elapsed vs a 600ms budget
    monkeypatch.setattr(rescore_mod.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(rescore_mod, "_generate", lambda t, c: "a corrected phrase")
    text = "a corrected phrase here"
    assert rescore(text, RescoreConfig(enabled=True, max_latency_ms=600)) == text


def test_within_latency_budget_applies(monkeypatch):
    ticks = iter([0.0, 0.1])  # 100ms elapsed, well under budget
    monkeypatch.setattr(rescore_mod.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(rescore_mod, "_generate", lambda t, c: "to the lawyers now")
    out = rescore("to the Warriors now", RescoreConfig(enabled=True, max_latency_ms=600))
    assert out == "to the lawyers now"


@pytest.mark.parametrize("pathological", [None, 12345, b"to the lawyers", ["a", "b"], 3.14])
def test_nonstring_generate_output_never_raises(monkeypatch, pathological):
    # The "never raises" claim must hold even when _generate returns a non-str
    # (None, int, bytes, list, float): the output is discarded, original kept.
    text = "send the letter to the lawyers"
    monkeypatch.setattr(rescore_mod, "_generate", lambda t, c: pathological)
    assert rescore(text, RescoreConfig(enabled=True)) == text


def test_moderate_expansion_now_rejected(monkeypatch):
    # The tightened 0.35 length guard must discard a rewrite that grows the text
    # well beyond a conservative fix (this would have passed the old 0.60 bound).
    text = "send the letter to the lawyers"  # 30 chars
    candidate = "please send the signed letter over to all of the lawyers today"  # ~2x
    monkeypatch.setattr(rescore_mod, "_generate", lambda t, c: candidate)
    assert rescore(text, RescoreConfig(enabled=True)) == text


def test_huge_output_returns_original(monkeypatch):
    text = "send the letter to the lawyers"
    monkeypatch.setattr(rescore_mod, "_generate", lambda t, c: "x" * 10_000_000)
    out = rescore(text, RescoreConfig(enabled=True))
    assert out == text  # runaway length guard discards it


def test_legit_correction_starting_with_refusal_word_is_kept(monkeypatch):
    # A real correction whose first word merely starts with a refusal token
    # ("Surely", "Sureshot") must NOT be discarded as a preamble. Regression
    # for the missing word boundary in the refusal regex.
    monkeypatch.setattr(rescore_mod, "_generate", lambda t, c: "Surely the deal closes")
    assert (
        rescore("Shirley the deal closes", RescoreConfig(enabled=True))
        == "Surely the deal closes"
    )
    monkeypatch.setattr(rescore_mod, "_generate", lambda t, c: "Sureshot logistics quote")
    assert (
        rescore("Sure shot logistics quote", RescoreConfig(enabled=True))
        == "Sureshot logistics quote"
    )


def test_punctuation_meta_labels_still_rejected(monkeypatch):
    # The boundary fix must not regress detection of "sorry," / "note:" /
    # "output:" / "result:" preambles.
    text = "the figures rose this quarter"
    for refusal in ("sorry, no", "note: see above", "output: foo", "result: foo"):
        monkeypatch.setattr(rescore_mod, "_generate", lambda t, c, r=refusal: r)
        assert rescore(text, RescoreConfig(enabled=True)) == text


def test_module_imports_without_mlx_lm():
    # Importing the module must not require the optional dependency.
    assert "mlx_lm" not in sys.modules


def test_load_caches_model(monkeypatch):
    # _load must load once and reuse — reloading per utterance would blow the
    # latency budget every call.
    import types

    calls = {"n": 0}
    fake = types.ModuleType("mlx_lm")

    def load(repo):
        calls["n"] += 1
        return ("model", "tok")

    fake.load = load
    monkeypatch.setitem(sys.modules, "mlx_lm", fake)
    monkeypatch.setattr(rescore_mod, "_MODELS", {})

    first = rescore_mod._load("repo/x")
    second = rescore_mod._load("repo/x")
    assert first == ("model", "tok")
    assert second is first
    assert calls["n"] == 1  # cached after the first load


def test_warm_up_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(
        rescore_mod, "_load",
        lambda m: (_ for _ in ()).throw(AssertionError("must not load when disabled")),
    )
    rescore_mod.warm_up(RescoreConfig(enabled=False))  # no load, no raise
    rescore_mod.warm_up(None)  # default config is disabled


def test_warm_up_never_raises(monkeypatch):
    def boom(model):
        raise RuntimeError("model load failed")

    monkeypatch.setattr(rescore_mod, "_load", boom)
    rescore_mod.warm_up(RescoreConfig(enabled=True))  # swallows, never propagates


def test_warm_up_loads_when_enabled(monkeypatch):
    seen = {}
    monkeypatch.setattr(rescore_mod, "_load", lambda m: seen.setdefault("model", m))
    rescore_mod.warm_up(RescoreConfig(enabled=True, model="repo/y"))
    assert seen["model"] == "repo/y"
