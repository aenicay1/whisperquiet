# whisperquiet 🤫

Whisper-quiet dictation + camera head/gesture control for macOS — your voice
does the typing, your webcam does the pointing. **Fully on-device: no audio,
no video, no text ever leaves your Mac.**

See [DESIGN.md](DESIGN.md) for the decision record and
[WHISPERFLOW_AGENT_LOG.md](WHISPERFLOW_AGENT_LOG.md) for build/verification
state.

## Install

```sh
git clone git@github.com:aenicay1/whisperquiet.git ~/Projects/whisperquiet
cd ~/Projects/whisperquiet
python3 -m venv .venv && .venv/bin/pip install -e .
bash scripts/make_app.sh        # builds ~/Applications/WhisperQuiet.app
open ~/Applications/WhisperQuiet.app
```

> Keep the repo **outside** `~/Documents` (TCC blocks app bundles from
> reading it). First run downloads the whisper model (~1.6 GB) and prompts
> for **Microphone**, **Accessibility**, and **Input Monitoring** — grant
> all three, then relaunch (macOS applies permissions at launch). Camera
> permission is requested when you first enable camera control.

## Use

| Action | How |
|---|---|
| Dictate | hold **right Option**, whisper, release — text lands in the focused app |
| Camera control on/off | 🤫 menu → *Camera Control*, or `touch "$HOME/Library/Application Support/whisperquiet/trigger-camera"` |
| Head cursor on/off | 🤫 menu → *Head Cursor*, or `…/trigger-cursor` |
| Left / right click | left / right **wink** |
| Scroll up / down | **raise brows** / **pucker** |
| Drag | **open mouth** to grab, close to drop |
| Re-run gesture calibration | `…/trigger-calibrate` |
| Quit (clears any stuck panel) | `…/trigger-quit` |

First camera-control activation runs a ~25-second guided calibration; your
personal gesture thresholds persist across launches. The HUD (top right)
shows live gesture meters, a face wireframe, status, and fps.

If the 🤫 icon is hidden (notch overflow), every control above also works
through the trigger files.

Config: `~/Library/Application Support/whisperquiet/config.json`
(PTT key, whisper model, streaming interval, injection mode, gesture data).

## Development

```sh
.venv/bin/pip install pytest
.venv/bin/python -m pytest tests/ -q
```

Pure-logic modules (gesture engine, calibration, cursor mapping, One-Euro
filter) are camera-free and deterministic — tests run headless.

## License

[MIT](LICENSE)
