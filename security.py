"""Authentification locale du panel GOAT.

- Un seul mot de passe, hache en PBKDF2-HMAC-SHA256 (310 000 iterations).
- Sessions sans etat : jeton signe HMAC-SHA256, valable 7 jours, survit aux redemarrages.
- Le secret et le hash sont stockes hors du depot (secret.key, auth.json).
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

import config

SESSION_COOKIE = "goat_session"
SESSION_TTL = 7 * 24 * 3600
PBKDF2_ITERATIONS = 310_000
MIN_PASSWORD_LENGTH = 8

MAX_ATTEMPTS = 10
ATTEMPT_WINDOW = 300

_AUTH_FILE = os.path.join(config.BASE_DIR, "auth.json")
_SECRET_FILE = os.path.join(config.BASE_DIR, "secret.key")

_secret = None
_failures = {}


def _write_private(path, content):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _read_secret():
    global _secret
    if _secret:
        return _secret
    try:
        with open(_SECRET_FILE, "r", encoding="utf-8") as f:
            value = f.read().strip()
        if len(value) >= 43:
            _secret = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            if len(_secret) >= 32:
                return _secret
    except (OSError, ValueError):
        pass
    _secret = secrets.token_bytes(32)
    _write_private(_SECRET_FILE, base64.urlsafe_b64encode(_secret).decode())
    return _secret


def _auth_data():
    try:
        with open(_AUTH_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("algo") == "pbkdf2_sha256" and data.get("hash") and data.get("salt"):
            return data
    except (OSError, ValueError):
        pass
    return None


def password_is_set():
    return _auth_data() is not None


def set_password(password):
    if len(password) < MIN_PASSWORD_LENGTH:
        return False, f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères."
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    data = {
        "algo": "pbkdf2_sha256",
        "iterations": PBKDF2_ITERATIONS,
        "salt": base64.b64encode(salt).decode(),
        "hash": base64.b64encode(digest).decode(),
        "updated": int(time.time()),
    }
    _write_private(_AUTH_FILE, json.dumps(data, indent=2))
    _failures.clear()
    return True, "Mot de passe enregistré."


def verify_password(password):
    data = _auth_data()
    if not data:
        return False
    try:
        salt = base64.b64decode(data["salt"])
        expected = base64.b64decode(data["hash"])
        rounds = int(data.get("iterations", PBKDF2_ITERATIONS))
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def rotate_secret():
    """Invalide toutes les sessions existantes."""
    global _secret
    _secret = secrets.token_bytes(32)
    _write_private(_SECRET_FILE, base64.urlsafe_b64encode(_secret).decode())


def issue_session(ttl=SESSION_TTL):
    expires = int(time.time()) + ttl
    nonce = secrets.token_urlsafe(16)
    payload = f"v1.{expires}.{nonce}"
    signature = hmac.new(_read_secret(), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{base64.urlsafe_b64encode(signature).decode().rstrip('=')}"


def valid_session(token):
    if not token or token.count(".") != 3:
        return False
    try:
        version, expires, nonce, signature_b64 = token.split(".")
        if version != "v1":
            return False
        if int(expires) < time.time():
            return False
        expected = hmac.new(
            _read_secret(), f"{version}.{expires}.{nonce}".encode(), hashlib.sha256
        ).digest()
        given = base64.urlsafe_b64decode(signature_b64 + "=" * (-len(signature_b64) % 4))
        return hmac.compare_digest(expected, given)
    except (ValueError, TypeError):
        return False


def too_many_attempts(client_ip):
    now = time.time()
    attempts = [t for t in _failures.get(client_ip, []) if now - t < ATTEMPT_WINDOW]
    _failures[client_ip] = attempts
    return len(attempts) >= MAX_ATTEMPTS


def record_failure(client_ip):
    _failures.setdefault(client_ip, []).append(time.time())


def clear_failures(client_ip):
    _failures.pop(client_ip, None)
