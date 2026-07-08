# WhisperQuiet

**Friends preview / download:** [aenicay1.github.io/whisperquiet-dist](https://aenicay1.github.io/whisperquiet-dist/)

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

By default, whisperquiet also keeps each dictation's audio (WAV, capped at
512MB / 30 days) and raw→cleaned transcript pairs on-device, to improve
accuracy over time. This stays local like everything else and can be turned
off via `keep_audio` / `keep_transcripts` in config.

## What it does today

- **Push-to-talk dictation** — hold **right Option**, speak or whisper, release;
  text is injected into the focused app.
- **Accuracy by default** — `whisper-large-v3-turbo` (MLX) is the default because
  word accuracy matters more than the smallest idle footprint.
- **Light mode** — switch to `whisper-small.en-mlx` from Preferences when you
  want lower memory use and can tolerate more recognition mistakes.
- **Personal vocabulary** — bias the names and jargon you actually use, editable
  from Preferences or the helper script.
- **Long-form** — silence-aware chunking + incremental streaming, so long
  brain-dumps don't drop sentences and release latency stays low.
- **Cleanup** — strips fillers and false starts and fixes obvious slips
  (time formats, repeated words).
- **Reliable** — survives the audio-device hot-swaps (AirPods/EarPods) that wedge
  macOS CoreAudio, without freezing the hotkey.
- **Experimental — camera head/gesture control** — webcam pointing plus
  wink/brow/mouth gestures for hands-free control. Off by default and currently
  frozen while dictation is the focus (see DESIGN.md, Pivot #2).

Everything runs locally. First launch downloads the default Accuracy model
(~1.5 GB) once; after that it works offline. The optional Light model is a
smaller one-time download (~459 MB).

## Install

```sh
git clone git@github.com:aenicay1/whisperquiet.git ~/Projects/whisperquiet
cd ~/Projects/whisperquiet
python3 -m venv .venv && .venv/bin/pip install -e .
bash scripts/make_app.sh        # builds ~/Applications/WhisperQuiet.app
open ~/Applications/WhisperQuiet.app
```

> Keep the repo **outside** `~/Documents` (TCC blocks app bundles from
> reading it). First run downloads the default whisper model (~1.5 GB) and prompts
> for **Microphone**, **Accessibility**, and **Input Monitoring** — grant
> all three, then relaunch (macOS applies permissions at launch). Camera
> permission is requested only if you enable the experimental camera control.

## Use

| Action | How |
|---|---|
| **Dictate** | hold **right Option**, whisper, release — text lands in the focused app |
| Open Preferences | WQ menu → *Preferences*, `wq-settings`, or `touch "$HOME/Library/Application Support/whisperquiet/trigger-settings"` |
| Switch model | WQ menu → *Use Light Model* / *Use Accuracy Model*, or Preferences → Models |
| Add a vocabulary term | Preferences → Dictionary, or `scripts/vocab.py add "EBITDA" "Circleback"` |
| Check memory/cache footprint | `scripts/cache.py status` |
| Dogfood scorecard | `scripts/report.py` |
| Quit (clears any stuck panel) | `…/trigger-quit` |

Preferences open once after this control surface lands, then stay available from
the menu/trigger paths. The app stays menu-bar/accessory by default because that
preserves macOS Accessibility/Input Monitoring grants; build with
`WQ_DOCK_ICON=1 bash scripts/make_app.sh` only when testing a clean Dock-visible
permission flow.

Config: `~/Library/Application Support/whisperquiet/config.json`
(PTT key, whisper model, streaming interval, injection mode, MLX memory caps,
audio retention, vocabulary).

### Experimental: camera control

Off by default and frozen for now (dictation is the priority). When enabled:

| Action | How |
|---|---|
| Camera control on/off | WQ menu → *Camera Control*, or `touch "$HOME/Library/Application Support/whisperquiet/trigger-camera"` |
| Head cursor on/off | WQ menu → *Head Cursor*, or `…/trigger-cursor` |
| Left / right click | left / right **wink** |
| Scroll up / down | **raise brows** / **pucker** |
| Drag | **open mouth** to grab, close to drop |
| Re-run gesture calibration | `…/trigger-calibrate` |

First activation runs a ~25-second guided calibration; your personal gesture
thresholds persist across launches. The HUD (top right) shows live gesture
meters, a face wireframe, status, and fps. If the WQ menu icon is hidden (notch
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
