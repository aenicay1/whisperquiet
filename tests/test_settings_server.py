import json
import urllib.error
import urllib.request

import pytest

from whisperquiet.settings_server import SettingsServer


def _start(get_state=None, apply_state=None, **kwargs):
    server = SettingsServer(
        get_state or (lambda: {}), apply_state or (lambda d: None), port=0, **kwargs
    )
    port = server.start()
    return server, f"http://127.0.0.1:{port}/config"


def _get_json(url):
    with urllib.request.urlopen(url, timeout=2) as resp:
        return resp.status, json.loads(resp.read())


def _post(url, body: bytes, headers=None):
    headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(
        url, data=body, headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=2) as resp:
        return resp.status, json.loads(resp.read())


def _base_url(config_url: str) -> str:
    return config_url.removesuffix("/config")


def test_get_returns_state_dict():
    state = {"stream_interval": 0.7, "cursor_gain": 2.5}
    server, url = _start(get_state=lambda: state)
    try:
        status, body = _get_json(url)
        assert status == 200
        assert body == state
    finally:
        server.stop()


def test_post_applies_parsed_dict():
    applied = []
    server, url = _start(apply_state=applied.append)
    try:
        status, body = _post(url, json.dumps({"stream_interval": 0.5, "gain": 3}).encode())
        assert status == 200
        assert body == {"ok": True, "applied": 2}
        assert applied == [{"stream_interval": 0.5, "gain": 3}]
    finally:
        server.stop()


def test_preferences_page_serves_bundled_html_with_token():
    server, url = _start(
        preferences_html="<html>token=__WQ_TOKEN__ port=__WQ_PORT__</html>"
    )
    try:
        with urllib.request.urlopen(_base_url(url) + "/", timeout=2) as resp:
            body = resp.read().decode()
        assert resp.status == 200
        assert f"token={server._token}" in body
        assert f"port={server._server.server_address[1]}" in body
    finally:
        server.stop()


def test_preferences_state_get_returns_preference_state():
    state = {"model": {"profile": "light"}, "dictionary": {"terms": ["WQ"]}}
    server, url = _start(get_preferences=lambda: state)
    try:
        status, body = _get_json(_base_url(url) + "/api/state")
        assert status == 200
        assert body == state
    finally:
        server.stop()


def test_preferences_post_requires_token():
    applied = []
    server, url = _start(apply_preferences=applied.append)
    api_url = _base_url(url) + "/api/state"
    try:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            _post(api_url, json.dumps({"model_profile": "accuracy"}).encode())
        assert excinfo.value.code == 403
        assert applied == []

        status, body = _post(
            api_url,
            json.dumps({"model_profile": "accuracy"}).encode(),
            headers={"X-WQ-Token": server._token},
        )
        assert status == 200
        assert body == {"ok": True, "applied": 1}
        assert applied == [{"model_profile": "accuracy"}]
    finally:
        server.stop()


def test_options_returns_cors_headers():
    server, url = _start()
    try:
        req = urllib.request.Request(url, method="OPTIONS")
        with urllib.request.urlopen(req, timeout=2) as resp:
            assert resp.status == 204
            assert resp.headers["Access-Control-Allow-Origin"] == "*"
            assert resp.headers["Access-Control-Allow-Methods"] == "GET, POST, OPTIONS"
            assert resp.headers["Access-Control-Allow-Headers"] == "Content-Type"
    finally:
        server.stop()


def test_malformed_post_is_400():
    applied = []
    server, url = _start(apply_state=applied.append)
    try:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            _post(url, b"{not json")
        assert excinfo.value.code == 400
        assert applied == []
    finally:
        server.stop()


def test_apply_error_is_500_and_server_survives():
    def explode(data):
        raise RuntimeError("bad gain value")

    server, url = _start(get_state=lambda: {"ok_state": 1}, apply_state=explode)
    try:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            _post(url, json.dumps({"gain": -1}).encode())
        assert excinfo.value.code == 500
        assert json.loads(excinfo.value.read()) == {"ok": False, "error": "bad gain value"}
        # The server thread must still be serving after the handler error.
        status, body = _get_json(url)
        assert status == 200
        assert body == {"ok_state": 1}
    finally:
        server.stop()


def test_occupied_port_falls_back_to_ephemeral():
    first, _ = _start()
    first_port = first._server.server_address[1]
    second = SettingsServer(lambda: {}, lambda d: None, port=first_port)
    try:
        second_port = second.start()
        assert second_port != first_port
        status, _ = _get_json(f"http://127.0.0.1:{second_port}/config")
        assert status == 200
    finally:
        second.stop()
        first.stop()


def test_stop_terminates_server():
    server, url = _start()
    server.stop()
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(url, timeout=2)
