"""Persist one-time PKCE attempts across the frame-to-phone QR handoff.

The phone need not share the frame's cookies or the process that displayed its QR.
Keep the verifier on the frame, never in the URL or a client-side cookie.
"""
import secrets
import time

from google_auth_oauthlib.flow import Flow

import config
import db

ATTEMPT_LIFETIME = 30 * 60
MAX_PENDING_ATTEMPTS = 32


def start_authorization(redirect_uri=None):
    configured = config.get_secrets().get("web", {}).get("redirect_uris", [])
    if not configured:
        raise ValueError("Google redirect URI is not configured")
    # Do not turn an untrusted Host/forwarded header into an OAuth destination.
    redirect_uri = redirect_uri if redirect_uri in configured else configured[0]
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    flow = Flow.from_client_secrets_file(
        config.SECRETS_PATH, scopes=config.SCOPES, redirect_uri=redirect_uri,
        state=state, code_verifier=verifier, autogenerate_code_verifier=False,
    )
    auth_url, _ = flow.authorization_url(
        state=state, prompt="consent", access_type="offline",
    )
    now = time.time()
    connection = db.get_db()
    with connection:
        connection.execute("DELETE FROM oauth_attempts WHERE expires_at <= ?", (now,))
        connection.execute(
            "INSERT INTO oauth_attempts VALUES (?, ?, ?, ?, ?)",
            (state, verifier, redirect_uri, flow.client_config["client_id"],
             now + ATTEMPT_LIFETIME),
        )
        connection.execute(
            "DELETE FROM oauth_attempts WHERE state IN "
            "(SELECT state FROM oauth_attempts ORDER BY expires_at DESC LIMIT -1 OFFSET ?)",
            (MAX_PENDING_ATTEMPTS,),
        )
    return auth_url


def setup_url():
    # A kiosk can display this QR indefinitely. Start PKCE only when the phone
    # follows the link, so expiry, consumption or pruning cannot stale the QR.
    return config.get_base_url() + "/auth"


def callback_flow(state):
    if not state or len(state) > 128:
        raise ValueError("Missing or invalid Google authorization state")
    connection = db.get_db()
    # Serialize consumption across concurrent callback requests/processes.
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM oauth_attempts WHERE state=?", (state,),
        ).fetchone()
        connection.execute("DELETE FROM oauth_attempts WHERE state=?", (state,))
    if row is None or row["expires_at"] <= time.time():
        raise ValueError("Google authorization expired or was already used")
    client = config.get_secrets().get("web", {})
    if (row["client_id"] != client.get("client_id")
            or row["redirect_uri"] not in client.get("redirect_uris", [])):
        raise ValueError("Google authorization configuration changed")
    return Flow.from_client_secrets_file(
        config.SECRETS_PATH, scopes=config.SCOPES,
        redirect_uri=row["redirect_uri"], state=state,
        code_verifier=row["verifier"], autogenerate_code_verifier=False,
    )
