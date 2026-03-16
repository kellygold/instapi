# auth.py - Authentication helpers
#
# Shared auth decorator and password verification, extracted to avoid
# circular imports between admin_routes and sync_routes.

import os
import getpass
import subprocess
from functools import wraps
from flask import session, redirect, url_for, jsonify, request


def require_admin(f):
    """Require admin authentication via session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_authenticated"):
            if request.is_json:
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


def verify_password(password):
    """Verify against system password or test override."""
    test_pw = os.environ.get("INSTAPI_ADMIN_PASSWORD")
    if test_pw:
        return password == test_pw
    # Production: verify via su command
    username = getpass.getuser()
    try:
        result = subprocess.run(
            ["/bin/su", "-c", "true", username],
            input=password + "\n",
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False
