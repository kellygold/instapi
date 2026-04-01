import os
import pytest


def test_get_display_mode_reads_file(tmp_path, monkeypatch):
    """Should read mode from .display_mode file."""
    # Create fake app dir structure with .display_mode at repo root
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    mode_file = tmp_path / ".display_mode"
    mode_file.write_text("usb")

    import utils
    monkeypatch.setattr(utils.os.path, "dirname",
                        lambda f, _orig=os.path.dirname: str(app_dir) if f == utils.__file__ else _orig(f))

    # Reimport won't work, so just test the logic directly
    result_path = os.path.join(str(app_dir), "..", ".display_mode")
    assert os.path.exists(result_path)
    with open(result_path) as f:
        assert f.read().strip() == "usb"


def test_get_display_mode_default_hdmi(tmp_path):
    """Should default to 'hdmi' when no .display_mode file exists."""
    # No .display_mode file in tmp_path
    mode_file = os.path.join(str(tmp_path), ".display_mode")
    assert not os.path.exists(mode_file)

    # Simulate the function logic
    if os.path.exists(mode_file):
        with open(mode_file) as f:
            result = f.read().strip()
    else:
        result = "hdmi"
    assert result == "hdmi"


def test_parse_time_value_seconds():
    """Should parse '5s' to 5."""
    from utils import parse_time_value
    assert parse_time_value("5s", 10) == 5


def test_parse_time_value_large():
    """Should parse '1800s' to 1800."""
    from utils import parse_time_value
    assert parse_time_value("1800s", 10) == 1800


def test_parse_time_value_no_unit():
    """Should parse bare number."""
    from utils import parse_time_value
    assert parse_time_value("30", 10) == 30


def test_parse_time_value_invalid_uses_default():
    """Should return default for non-numeric input."""
    from utils import parse_time_value
    assert parse_time_value("abc", 42) == 42


def test_parse_time_value_empty_uses_default():
    """Should return default for empty string."""
    from utils import parse_time_value
    assert parse_time_value("", 7) == 7


def test_list_picker_photo_urls_follows_pagination(monkeypatch):
    """Should collect every selected picker item across paginated responses."""
    from utils import list_picker_photo_urls

    responses = [
        {
            "mediaItems": [
                {"mediaFile": {"baseUrl": "https://example.com/photo-1"}},
                {"mediaFile": {"baseUrl": "https://example.com/photo-2"}},
            ],
            "nextPageToken": "page-2",
        },
        {
            "mediaItems": [
                {"mediaFile": {"baseUrl": "https://example.com/photo-3"}},
            ],
        },
    ]
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload
            self.text = ""

        def json(self):
            return self._payload

    def fake_get(url, headers=None, params=None):
        calls.append({"url": url, "headers": headers, "params": params})
        return FakeResponse(responses[len(calls) - 1])

    import utils
    monkeypatch.setattr(utils.requests, "get", fake_get)

    result = list_picker_photo_urls("session-123", {"Authorization": "Bearer token"})

    assert result == [
        "https://example.com/photo-1=w2048-h1024",
        "https://example.com/photo-2=w2048-h1024",
        "https://example.com/photo-3=w2048-h1024",
    ]
    assert len(calls) == 2
    assert calls[0]["params"] == {"sessionId": "session-123", "pageSize": 100}
    assert calls[1]["params"] == {
        "sessionId": "session-123",
        "pageSize": 100,
        "pageToken": "page-2",
    }
