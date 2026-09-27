import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlencode

import boto3

API = 'https://api.workos.com'
_ssm = boto3.client('ssm')


def get_api_key():
    param = _ssm.get_parameter(Name=os.environ['WORKOS_API_KEY_PARAM'], WithDecryption=True)
    value = param['Parameter']['Value'].strip()
    return json.loads(value)['api_key'] if value.startswith('{') else value


def workos(api_key, method, path, body=None):
    req = urllib.request.Request(
        f'{API}{path}',
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            'authorization': f'Bearer {api_key}',
            'content-type': 'application/json',
            'user-agent': 'karaoke-workos-bootstrap',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            text = res.read().decode()
    except urllib.error.HTTPError as err:
        raise RuntimeError(f'WorkOS {method} {path} failed: {err.code} {err.read().decode(errors="replace")}') from None
    return json.loads(text) if text else {}


def list_all(api_key, path):
    after = None
    while True:
        qs = urlencode({'limit': '100', **({'after': after} if after else {})})
        page = workos(api_key, 'GET', f'{path}?{qs}')
        yield from page['data']
        after = (page.get('list_metadata') or {}).get('after')
        if not after:
            return


def find_by_name(api_key, path, name):
    return next((item for item in list_all(api_key, path) if item.get('name') == name), None)


# Registers redirect_uri with User Management so the Google login callback is accepted.
# Other redirect URIs already registered, e.g. local development callbacks, are kept.
def ensure_redirect_uri(api_key, redirect_uri):
    if not any(item.get('uri') == redirect_uri for item in list_all(api_key, '/user_management/redirect_uris')):
        workos(api_key, 'POST', '/user_management/redirect_uris', {'uri': redirect_uri})
    return [item['uri'] for item in list_all(api_key, '/user_management/redirect_uris')]


def handler(event, context):
    organization_name = event['organizationName']
    redirect_uri = event['redirectUri']
    api_key = get_api_key()

    organization = find_by_name(api_key, '/organizations', organization_name) or workos(
        api_key, 'POST', '/organizations', {'name': organization_name}
    )

    return {
        'organization_id': organization['id'],
        'redirect_uris': ensure_redirect_uri(api_key, redirect_uri),
    }
