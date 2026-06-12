"""Manage the dictation vocabulary (decoder biasing).

Usage:
  vocab.py add "EBITDA" "Circleback" ...   add terms (staccato-formatted)
  vocab.py list                            show current vocabulary
  vocab.py rm "term"                       remove a term
Changes apply on next dictation (config is re-read by the running app on
each transcription via Config object refresh at relaunch; relaunch to be
safe).
"""
import json
import sys
from pathlib import Path

P = Path.home() / "Library" / "Application Support" / "whisperquiet" / "config.json"


def main() -> int:
    cfg = json.loads(P.read_text())
    vocab = cfg.setdefault("vocabulary", [])
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
    P.write_text(json.dumps(cfg, indent=2))
    print(f"{len(vocab)} terms. Restart the app (wq-quit; wq-start) to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
