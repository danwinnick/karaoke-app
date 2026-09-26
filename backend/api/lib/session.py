import base64
import hashlib
import hmac
import json
import time


def b64url_encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def b64url_decode(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def _sign(secret, data):
    return b64url_encode(hmac.new(secret.encode(), data.encode(), hashlib.sha256).digest())


def sign_token(payload, secret, ttl_seconds):
    claims = {**payload, 'exp': int(time.time()) + ttl_seconds}
    body = b64url_encode(json.dumps(claims, separators=(',', ':')).encode())
    return f'{body}.{_sign(secret, body)}'


def verify_token(token, secret):
    if not isinstance(token, str):
        return None
    parts = token.split('.')
    body, sig = parts[0], parts[1] if len(parts) > 1 else ''
    if not body or not sig:
        return None
    if not hmac.compare_digest(_sign(secret, body).encode(), sig.encode()):
        return None
    try:
        payload = json.loads(b64url_decode(body))
    except ValueError:
        return None
    exp = payload.get('exp') if isinstance(payload, dict) else None
    return payload if isinstance(exp, (int, float)) and exp > time.time() else None
