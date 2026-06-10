"""Global push-to-talk key listener. Requires the Input Monitoring permission."""

from __future__ import annotations

from collections.abc import Callable

from pynput import keyboard


def resolve_key(name: str):
    """Map a config key name ('alt_r', 'f13', …) to a pynput key object."""
    try:
        return keyboard.Key[name]
    except KeyError:
        return keyboard.KeyCode.from_char(name)


class PushToTalk:
    def __init__(
        self,
        key_name: str,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
    ) -> None:
        self._key = resolve_key(key_name)
        self._on_press = on_press
        self._on_release = on_release
        self._held = False
        self._listener = keyboard.Listener(
            on_press=self._handle_press, on_release=self._handle_release
        )

    def start(self) -> None:
        self._listener.start()

    def _handle_press(self, key) -> None:
        # macOS auto-repeats key-down while held; only fire on the edge
        if key == self._key and not self._held:
            self._held = True
            self._on_press()

    def _handle_release(self, key) -> None:
        if key == self._key and self._held:
            self._held = False
            self._on_release()
