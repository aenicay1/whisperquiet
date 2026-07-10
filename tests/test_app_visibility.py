from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dev_wrapper_uses_wq_icon_and_keeps_dock_mode_opt_in():
    text = (ROOT / "scripts" / "make_app.sh").read_text()

    assert "<key>CFBundleIconFile</key>" in text
    assert "whisperquiet.icns" in text
    assert "WQ_DOCK_ICON" in text
    assert "<key>LSUIElement</key>" in text


def test_distribution_build_keeps_dock_mode_opt_in():
    text = (ROOT / "packaging" / "whisperquiet.spec").read_text()

    assert "WQ_DOCK_ICON" in text
    assert '"LSUIElement": os.environ.get("WQ_DOCK_ICON", "0") != "1"' in text


def test_app_sets_explicit_menu_bar_title():
    text = (ROOT / "whisperquiet" / "app.py").read_text()

    assert 'super().__init__("WQ", title="WQ", quit_button="Quit")' in text


def test_notch_indicator_joins_spaces_and_fullscreen_apps():
    text = (ROOT / "whisperquiet" / "overlay.py").read_text()

    assert "NSWindowCollectionBehaviorCanJoinAllSpaces" in text
    assert "NSWindowCollectionBehaviorFullScreenAuxiliary" in text
    assert "NSWindowCollectionBehaviorStationary" in text
    assert "setCollectionBehavior_" in text
