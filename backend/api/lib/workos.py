import hashlib
import json
import os
import secrets
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

import boto3

from lib.session import b64url_encode

API = 'https://api.workos.com'
USER_AGENT = 'karaoke-api'
_ssm = boto3.client('ssm')
_api_key = None


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


def pkce_pair():
    verifier = b64url_encode(secrets.token_bytes(32))
    challenge = b64url_encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


# Singers sign in with Google only, so the authorize URL skips AuthKit's method picker
# and goes straight to Google. WORKOS_CLIENT_ID is the environment's client ID.
def authorize_url(state, challenge, redirect_uri):
    params = {
        'response_type': 'code',
        'client_id': os.environ['WORKOS_CLIENT_ID'],
        'redirect_uri': redirect_uri,
        'provider': 'GoogleOAuth',
        'state': state,
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
    }
    return f'{API}/user_management/authorize?{urlencode(params)}'


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


# Returns {'user': {...}, 'authentication_method': 'GoogleOAuth', ...}.
def authenticate_code(code, verifier):
    return _workos('POST', '/user_management/authenticate', {
        'client_id': os.environ['WORKOS_CLIENT_ID'],
        'client_secret': get_api_key(),
        'grant_type': 'authorization_code',
        'code': code,
        'code_verifier': verifier,
    })


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


def _find_user(email, organization_id=None):
    params = {'email': email, 'limit': 1}
    if organization_id:
        params['organization_id'] = organization_id
    users = _workos('GET', f'/user_management/users?{urlencode(params, quote_via=quote)}').get('data') or []
    return users[0] if users else None


# The WorkOS user with this email if they belong to the deployment's organization, else None.
def find_org_user(email):
    return _find_user(email, os.environ['WORKOS_ORG_ID'])


# Signs up a DJ whose email was just verified with a login code: reuses their WorkOS user if
# one exists, otherwise creates it, then adds them to the deployment's organization.
def create_org_user(email, first_name, last_name):
    user = _find_user(email) or _workos('POST', '/user_management/users', {
        'email': email,
        'first_name': first_name,
        'last_name': last_name or None,
        'email_verified': True,
    })
    ensure_membership(user['id'])
    return user
