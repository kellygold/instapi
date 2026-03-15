"""Simple in-memory rate limiter. No dependencies."""
import time
from functools import wraps
from flask import request, jsonify

# {ip: [timestamp, timestamp, ...]}
_attempts = {}


def rate_limit(max_attempts=5, window_seconds=60, message="Too many attempts. Try again later."):
    """Decorator to rate-limit an endpoint by client IP.

    Usage:
        @app.route("/login", methods=["POST"])
        @rate_limit(max_attempts=5, window_seconds=60)
        def login():
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            ip = request.remote_addr or "unknown"
            now = time.time()
            cutoff = now - window_seconds

            # Clean old entries for this IP
            if ip in _attempts:
                _attempts[ip] = [t for t in _attempts[ip] if t > cutoff]
            else:
                _attempts[ip] = []

            if len(_attempts[ip]) >= max_attempts:
                return jsonify({"error": message}), 429

            _attempts[ip].append(now)
            return f(*args, **kwargs)
        return decorated
    return decorator


def clear_rate_limit(ip=None):
    """Clear rate limit for an IP (e.g., after successful login)."""
    if ip:
        _attempts.pop(ip, None)
    else:
        _attempts.clear()
