# whisperquiet 🤫

**Private, on-device dictation for macOS — like [Wispr Flow](https://wisprflow.ai),
but free and fully local.** Hold a key, (whisper-)speak, release — your words land
in whatever app is focused. No subscription, no account, and **nothing — no audio,
no video, no text — ever leaves your Mac.**

Built for Apple Silicon, and tuned to work even when you *whisper*, so you can
dictate in a quiet office, a library, or next to a sleeping kid.

See [DESIGN.md](DESIGN.md) for the decision record and
[docs/BACKLOG.md](docs/BACKLOG.md) for the live roadmap.

## Why

Wispr Flow nailed the experience: one hotkey, fast transcription, text dropped
into any app. But it's a paid subscription and your speech is processed in the
cloud. whisperquiet is that same fast push-to-talk dictation running entirely
on-device against a local Whisper model — free, private, and offline-capable.

## What it does today

- **Push-to-talk dictation** — hold **right Option**, speak or whisper, release;
  text is injected into the focused app.
- **Accurate** — `whisper-large-v3-turbo` (MLX) plus a personal vocabulary that
  biases the names and jargon you actually use. Near-perfect on quiet speech and
  strong on whispered speech.
- **Long-form** — silence-aware chunking + incremental streaming, so long
  brain-dumps don't drop sentences and release latency stays low.
- **Cleanup** — strips fillers and false starts and fixes obvious slips
  (time formats, repeated words).
- **Reliable** — survives the audio-device hot-swaps (AirPods/EarPods) that wedge
  macOS CoreAudio, without freezing the hotkey.
- **Experimental — camera head/gesture control** — webcam pointing plus
  wink/brow/mouth gestures for hands-free control. Off by default and currently
  frozen while dictation is the focus (see DESIGN.md, Pivot #2).

Everything runs locally. First launch downloads the Whisper model (~1.6 GB) once;
after that it works offline.

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
> permission is requested only if you enable the experimental camera control.

## Use

| Action | How |
|---|---|
| **Dictate** | hold **right Option**, whisper, release — text lands in the focused app |
| Add a vocabulary term | `scripts/vocab.py add "EBITDA" "Circleback"` (biases the decoder) |
| Quit (clears any stuck panel) | `…/trigger-quit` |

Config: `~/Library/Application Support/whisperquiet/config.json`
(PTT key, whisper model, streaming interval, injection mode, vocabulary).

### Experimental: camera control

Off by default and frozen for now (dictation is the priority). When enabled:

| Action | How |
|---|---|
| Camera control on/off | 🤫 menu → *Camera Control*, or `touch "$HOME/Library/Application Support/whisperquiet/trigger-camera"` |
| Head cursor on/off | 🤫 menu → *Head Cursor*, or `…/trigger-cursor` |
| Left / right click | left / right **wink** |
| Scroll up / down | **raise brows** / **pucker** |
| Drag | **open mouth** to grab, close to drop |
| Re-run gesture calibration | `…/trigger-calibrate` |

First activation runs a ~25-second guided calibration; your personal gesture
thresholds persist across launches. The HUD (top right) shows live gesture
meters, a face wireframe, status, and fps. If the 🤫 icon is hidden (notch
overflow), every control also works through the trigger files.

## Development

```sh
.venv/bin/pip install pytest
.venv/bin/python -m pytest tests/ -q
```

Pure-logic modules (dictation chunking, cleanup, gesture engine, calibration,
cursor mapping, One-Euro filter) are device-free and deterministic — tests run
headless.

## License

[MIT](LICENSE)
