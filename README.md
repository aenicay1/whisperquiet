# whisperquiet 🤫

Whisper-quiet dictation + camera head/gesture control for macOS. Your voice
does the typing, your webcam does the pointing — fully on-device.

See [DESIGN.md](DESIGN.md) for the decision record and roadmap. Current state:
**week-1 skeleton** — push-to-talk dictation streaming into a floating
overlay, committed into the focused app on release. Camera control lands in
week 3–4.

## Setup

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
whisperquiet
```

First run downloads the whisper model (~1.6 GB for large-v3-turbo) and will
prompt for permissions:

- **Microphone** — audio capture
- **Input Monitoring** — the global push-to-talk key (pynput)
- **Accessibility** — injecting the final text (Quartz events)

Grant them to your terminal (or whatever launches the app) in
System Settings → Privacy & Security, then restart the app.

## Use

Hold **right Option** (default), whisper, release. Partial text streams into
the overlay while you speak; the final transcription is typed into whatever
app has focus when you let go.

Config lives at `~/Library/Application Support/whisperquiet/config.json`:

| key | default | notes |
|-----|---------|-------|
| `ptt_key` | `alt_r` | pynput key name (`f13` is great if you have one) |
| `model_repo` | `mlx-community/whisper-large-v3-turbo` | any MLX whisper repo |
| `language` | `en` | |
| `stream_interval` | `0.7` | seconds between partial re-transcriptions |
| `inject_mode` | `keystrokes` | `paste` for apps that drop synthetic keys |

## Development

```sh
pip install pytest
pytest
```
