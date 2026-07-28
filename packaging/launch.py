"""Frozen-app entry point.

PyInstaller bundles *this* file as the executable and traces its imports to
decide what to freeze (see packaging/whisperquiet.spec). It hands off to
whisperquiet.app.main so the bundle behaves like `python -m whisperquiet.app`.

multiprocessing.freeze_support() MUST be the first thing that runs. On macOS the
default start method is 'spawn', so a dependency that uses multiprocessing (e.g.
tqdm lazily creating an mp RLock, behind the resource tracker) re-executes THIS
frozen binary for each child. Without freeze_support() the child falls through to
main() and launches a whole second app — which spawns more children — a fork
bomb (observed: the startup banner repeating with new ports + a swarm of
resource_tracker processes). freeze_support() makes a spawned child run only its
intended target and exit, breaking the chain. (Not switching to 'fork': the app
already has live Cocoa/Metal/threads, and forking after that is unsafe.)
"""

import multiprocessing
import os
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()

    # A bundle launched from Finder has no terminal: stdout/stderr go to a dead
    # fd, so every print(flush=True) diagnostic — and native crash output from
    # mlx/Metal/PortAudio — silently vanishes. The dev wrapper (make_app.sh)
    # redirected to ~/Library/Logs/whisperquiet.log; preserve that so a production
    # failure stays diagnosable instead of disappearing (fail loud, never silently
    # drop). fd-level dup2 also captures C-library output, not just Python prints.
    # Only for the frozen app; WQ_NO_LOG_REDIRECT lets the build's smoke test read
    # stdout directly. Best-effort: never block launch if the log can't open.
    if getattr(sys, "frozen", False) and not os.environ.get("WQ_NO_LOG_REDIRECT"):
        try:
            log_dir = os.path.expanduser("~/Library/Logs")
            os.makedirs(log_dir, exist_ok=True)
            _log = open(os.path.join(log_dir, "whisperquiet.log"), "a", buffering=1)
            os.dup2(_log.fileno(), 1)
            os.dup2(_log.fileno(), 2)
        except OSError:
            pass

    # Build-time production probe: exercise multiprocessing spawn + bundled
    # sounddevice/PortAudio + the real default mic without launching the UI or
    # loading Whisper. The environment is inherited by the spawned child, but
    # freeze_support() consumes its multiprocessing invocation before execution
    # reaches this branch, so it cannot recurse into another probe.
    if os.environ.get("WQ_AUDIO_PROBE"):
        import time

        from whisperquiet.audio_process import ProcessMicRecorder

        recorder = ProcessMicRecorder()
        recorder.start(open_timeout=8.0)
        time.sleep(0.25)
        audio = recorder.stop(close_timeout=2.0)
        if not audio.size:
            raise SystemExit("audio child smoke FAIL: no samples")
        print(
            f"audio child smoke PASS: samples={audio.size}",
            flush=True,
        )
        raise SystemExit(0)

    from whisperquiet.app import main

    main()
