# WhisperQuiet preview to-do list

Last updated: 2026-06-29.

## Ship before sharing the friends preview
- Rebuild `dist/WhisperQuiet.app` from the current branch.
- Smoke-launch the frozen app and confirm one startup, model warm-up, and PTT tap install.
- Zip the app as `WhisperQuiet.zip` and publish it to `aenicay1/whisperquiet-dist` as `v0.1.0-preview`.
- Deploy `site/` and verify the download button fetches that ZIP.
- Document the checksum next to the release asset.

## Credential / clean-Mac gates
- Notarization is blocked until there is an Apple Developer ID certificate.
- The friends preview is ad-hoc signed, so first launch requires right-click `Open`.
- A true clean-Mac install test still needs a separate Apple Silicon Mac or VM with no repo or venv.

## Product follow-ups after preview
- Add visible download progress during first-run model download.
- Decide whether to delete old experiment model caches manually:
  `mlx-community/whisper-large-v3-mlx`, `mlx-community/parakeet-tdt-0.6b-v3`,
  and `mlx-community/Qwen2.5-1.5B-Instruct-4bit`.
- Dogfood the new MLX memory counters in `scripts/report.py` after running the updated app.
- Record fresh quiet, noisy, and whispered reference reads for WER comparison.
- Keep camera controls frozen unless a measured lip-reading or head-control feature earns its way back in.
