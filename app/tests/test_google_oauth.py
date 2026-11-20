import base64
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import pytest
import requests


@pytest.fixture(autouse=True)
def isolated_oauth_config(monkeypatch, tmp_path, mock_secrets):
    import config
    path = tmp_path / "oauth-client.json"
    path.write_text(json.dumps(mock_secrets))
    monkeypatch.setattr(config, "SECRETS_PATH", str(path))
    monkeypatch.setattr(config, "_secrets_cache", None)


def query(url):
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


@pytest.fixture
def token_endpoint(monkeypatch):
    calls = []

    def send(session, request, **kwargs):
        assert request.url == "https://oauth2.googleapis.com/token"
        calls.append(query("https://test.invalid/?" + request.body))
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        response.request = request
        response._content = json.dumps({
            "access_token": "synthetic-access-token", "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/photospicker.mediaitems.readonly",
        }).encode()
        response.headers["Content-Type"] = "application/json"
        return response

    monkeypatch.setattr(requests.sessions.Session, "send", send)
    return calls


def test_qr_handoff_survives_idle_screen_restart_and_separate_phone(app_client, token_endpoint, monkeypatch):
    import db
    from app import app

    page = app_client.get("/").get_data(as_text=True)
    assert 'href="http://localhost:3000/auth"' in page
    # The frame may sit for days. Its unchanged link starts consent on the phone.
    later = time.time() + 7 * 86400
    monkeypatch.setattr("google_oauth.time.time", lambda: later)
    with app.test_client(use_cookies=False) as phone:
        auth = query(phone.get("/auth").location)
        assert auth["code_challenge_method"] == "S256"
        db._local.conn.close()
        db._local.conn = None
        response = phone.get("/oauth2callback", query_string={
            "state": auth["state"], "code": "synthetic-code",
        })
        assert response.status_code == 302
        assert response.location.endswith("/launch_picker")
        assert phone.get("/oauth2callback", query_string={
            "state": auth["state"], "code": "synthetic-code",
        }).status_code == 400
    assert len(token_endpoint) == 1
    sent = token_endpoint[0]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(sent["code_verifier"].encode()).digest()
    ).decode().rstrip("=")
    assert challenge == auth["code_challenge"]
    assert sent["redirect_uri"] == auth["redirect_uri"]
    assert db.get_setting("credentials") == {"token": "synthetic-access-token"}


@pytest.mark.parametrize("state", [None, "unknown-state", "x" * 129])
def test_invalid_state_never_exchanges_or_replaces_credentials(app_client, token_endpoint, state):
    import db
    db.set_setting("credentials", {"token": "existing"})
    args = {"code": "synthetic-code"}
    if state is not None:
        args["state"] = state
    assert app_client.get("/oauth2callback", query_string=args).status_code == 400
    assert token_endpoint == []
    assert db.get_setting("credentials") == {"token": "existing"}


def test_expired_and_consumed_attempts_do_not_stale_qr(app_client, token_endpoint, monkeypatch):
    import db
    import qrcode
    urls = []
    make = qrcode.make

    def capture(url):
        urls.append(url)
        return make(url)

    monkeypatch.setattr(qrcode, "make", capture)
    db.set_setting("auth_url", "https://accounts.google.com/?state=old-no-verifier")
    assert "old-no-verifier" not in app_client.get("/").get_data(as_text=True)
    assert app_client.get("/auth_qr").status_code == 200
    first = query(app_client.get("/auth").location)
    db.get_db().execute("UPDATE oauth_attempts SET expires_at=?", (time.time() - 1,))
    db.get_db().commit()
    assert app_client.get("/auth_qr").status_code == 200
    assert app_client.get("/oauth2callback", query_string={
        "state": first["state"], "code": "synthetic-code",
    }).status_code == 400
    assert token_endpoint == []
    second = query(app_client.get("/auth").location)
    assert app_client.get("/oauth2callback", query_string={
        "state": second["state"], "code": "synthetic-code",
    }).status_code == 302
    assert app_client.get("/auth_qr").status_code == 200
    third = query(app_client.get("/auth").location)
    assert third["state"] not in [first["state"], second["state"]]
    assert urls == ["http://localhost:3000/auth"] * 3


def test_cancelled_reconnect_keeps_existing_photos_connection_and_picker(app_client, token_endpoint):
    import db
    db.set_setting("credentials", {"token": "existing"})
    db.set_setting("picking_session_id", "existing-picker")
    db.add_photo("existing.jpg")
    auth = query(app_client.get("/auth").location)
    assert db.get_setting("credentials") == {"token": "existing"}
    assert app_client.get("/oauth2callback", query_string={
        "state": auth["state"], "error": "access_denied",
    }).status_code == 400
    assert token_endpoint == []
    assert db.get_setting("credentials") == {"token": "existing"}
    assert db.get_setting("picking_session_id") == "existing-picker"
    assert db.get_photo_count() == 1


def test_independent_attempts_restore_their_original_redirects(app_client, token_endpoint):
    import config
    from google_oauth import start_authorization
    alternate = "https://frame.example/oauth2callback"
    config.get_secrets()["web"]["redirect_uris"].append(alternate)
    first = query(start_authorization())
    second = query(start_authorization(alternate))
    for auth in [second, first]:
        assert app_client.get("/oauth2callback", query_string={
            "state": auth["state"], "code": "synthetic-code",
        }).status_code == 302
    assert [call["redirect_uri"] for call in token_endpoint] == [
        alternate, first["redirect_uri"],
    ]
    assert token_endpoint[0]["code_verifier"] != token_endpoint[1]["code_verifier"]


def test_admin_link_stays_valid_and_ignores_unconfigured_forwarded_host(app_client):
    import db
    response = app_client.get("/admin", headers={"X-Forwarded-Host": "evil.ngrok-free.dev"})
    assert response.status_code == 200
    assert 'href="http://localhost:3000/auth"' in response.get_data(as_text=True)
    assert db.get_db().execute("SELECT COUNT(*) FROM oauth_attempts").fetchone()[0] == 0
    auth = query(app_client.get("/auth", headers={
        "Host": "evil.ngrok-free.dev", "X-Forwarded-Host": "evil.ngrok-free.dev",
    }).location)
    assert auth["redirect_uri"] == "http://localhost:3000/oauth2callback"
    assert auth["code_challenge_method"] == "S256"


def test_concurrent_callbacks_consume_attempt_once(app_client):
    import db
    from google_oauth import callback_flow, start_authorization
    state = query(start_authorization())["state"]

    def consume(_):
        try:
            callback_flow(state)
            return True
        except ValueError:
            return False
        finally:
            db._local.conn.close()
            db._local.conn = None

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(consume, range(2))) == [False, True]


def test_reset_invalidates_pending_consent(app_client, token_endpoint):
    import db
    from google_oauth import start_authorization
    state = query(start_authorization())["state"]
    db.clear_all_settings()
    assert app_client.get("/oauth2callback", query_string={
        "state": state, "code": "synthetic-code",
    }).status_code == 400
    assert token_endpoint == []
