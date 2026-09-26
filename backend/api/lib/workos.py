import hashlib
import json
import os
import secrets
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

import boto3

from lib.session import b64url_decode, b64url_encode

API = 'https://api.workos.com'
USER_AGENT = 'karaoke-api'
_ssm = boto3.client('ssm')
_api_key = None
_oidc = None


class WorkOSError(Exception):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


def _request(method, url, body=None, headers=None):
    req = urllib.request.Request(url, data=body, method=method, headers={'user-agent': USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, res.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode(errors='replace')


def get_api_key():
    global _api_key
    if _api_key is None:
        param = _ssm.get_parameter(Name=os.environ['WORKOS_API_KEY_PARAM'], WithDecryption=True)
        value = param['Parameter']['Value'].strip()
        _api_key = json.loads(value)['api_key'] if value.startswith('{') else value
    return _api_key


# AuthKit acts as the OAuth/OIDC provider. WORKOS_CLIENT_ID comes from the SSM
# parameter /karaoke/workos_client_id.
def _oidc_config():
    global _oidc
    if _oidc is None:
        issuer = f"https://{os.environ['AUTHKIT_DOMAIN']}"
        cfg = {}
        try:
            status, text = _request('GET', f'{issuer}/.well-known/openid-configuration')
            if 200 <= status < 300:
                cfg = json.loads(text)
        except Exception:
            pass
        _oidc = {
            'authorization_endpoint': cfg.get('authorization_endpoint') or f'{issuer}/oauth2/authorize',
            'token_endpoint': cfg.get('token_endpoint') or f'{issuer}/oauth2/token',
        }
    return _oidc


def pkce_pair():
    verifier = b64url_encode(secrets.token_bytes(32))
    challenge = b64url_encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def authorize_url(state, challenge, redirect_uri, signup=False):
    params = {
        'response_type': 'code',
        'client_id': os.environ['WORKOS_CLIENT_ID'],
        'redirect_uri': redirect_uri,
        'scope': 'openid profile email',
        'state': state,
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
    }
    if signup:
        params['screen_hint'] = 'sign-up'
    return f"{_oidc_config()['authorization_endpoint']}?{urlencode(params)}"


def exchange_code(code, verifier, redirect_uri):
    body = urlencode({
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
        'client_id': os.environ['WORKOS_CLIENT_ID'],
        'code_verifier': verifier,
    }).encode()
    status, text = _request(
        'POST', _oidc_config()['token_endpoint'], body, {'content-type': 'application/x-www-form-urlencoded'}
    )
    if not 200 <= status < 300:
        raise RuntimeError(f'Token exchange failed: {status} {text}')
    return json.loads(text)


# The id_token comes straight from the token endpoint over TLS, so its claims can be
# trusted without re-verifying the signature (OIDC Core 3.1.3.7).
def decode_jwt(token):
    return json.loads(b64url_decode(token.split('.')[1]))


def _workos(method, path, body=None):
    status, text = _request(
        method,
        f'{API}{path}',
        json.dumps(body).encode() if body is not None else None,
        {'authorization': f'Bearer {get_api_key()}', 'content-type': 'application/json'},
    )
    if not 200 <= status < 300:
        raise WorkOSError(f'WorkOS {method} {path} failed: {status} {text}', status)
    return json.loads(text) if text else {}


def get_user(user_id):
    return _workos('GET', f"/user_management/users/{quote(user_id, safe='')}")


# Everyone who signs in joins the deployment's organization. Already-a-member errors are ignored.
def ensure_membership(user_id):
    try:
        _workos('POST', '/user_management/organization_memberships', {
            'user_id': user_id,
            'organization_id': os.environ['WORKOS_ORG_ID'],
        })
    except WorkOSError as err:
        if not 400 <= err.status < 500:
            raise
