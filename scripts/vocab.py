"""Manage the dictation vocabulary (decoder biasing).

Usage:
  vocab.py add "EBITDA" "Circleback" ...   add terms (staccato-formatted)
  vocab.py list                            show current vocabulary
  vocab.py rm "term"                       remove a term
Changes apply on next dictation (config is re-read by the running app on
each transcription via Config object refresh at relaunch; relaunch to be
safe).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from whisperquiet import config as config_mod  # noqa: E402


def main() -> int:
    # go through config_mod.load/save so the write is atomic (tempfile +
    # os.replace) — writing config.json directly here can interleave with the
    # running app's settings-server saves and corrupt it.
    cfg = config_mod.load()
    vocab = cfg.vocabulary
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "add":
        for term in sys.argv[2:]:
            entry = term.strip().rstrip(".") + "."  # staccato form won the WER grid
            if entry not in vocab:
                vocab.append(entry)
                print("added:", entry)
    elif cmd == "rm":
        for term in sys.argv[2:]:
            entry = term.strip().rstrip(".") + "."
            if entry in vocab:
                vocab.remove(entry)
                print("removed:", entry)
    else:
        print("\n".join(vocab) or "(empty)")
        return 0
    config_mod.save(cfg)
    print(f"{len(vocab)} terms. Restart the app (wq-quit; wq-start) to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
