import json
import os
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

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


def find_by_name(api_key, path, name):
    after = None
    while True:
        qs = urlencode({'limit': '100', **({'after': after} if after else {})})
        page = workos(api_key, 'GET', f'{path}?{qs}')
        match = next((item for item in page['data'] if item.get('name') == name), None)
        if match:
            return match
        after = (page.get('list_metadata') or {}).get('after')
        if not after:
            return None


# Registers redirect_uri as the default redirect on the OAuth application that owns
# client_id (the one the API signs users in with). Other redirect URIs already on the
# application, e.g. local development callbacks, are kept.
def register_redirect_uri(api_key, client_id, redirect_uri):
    try:
        application = workos(api_key, 'GET', f"/connect/applications/{quote(client_id, safe='')}")
    except RuntimeError as err:
        raise RuntimeError(
            f'Could not find the WorkOS OAuth application for client ID {client_id} '
            '(SSM /karaoke/workos_client_id). It must be the client ID of a WorkOS Connect '
            f'OAuth application in the same environment as the API key. {err}'
        ) from None

    redirect_uris = [{'uri': redirect_uri, 'default': True}] + [
        {'uri': item['uri'], 'default': False}
        for item in application.get('redirect_uris') or []
        if item.get('uri') and item['uri'] != redirect_uri
    ]
    return workos(api_key, 'PUT', f"/connect/applications/{application['id']}", {
        'redirect_uris': redirect_uris,
    })


def handler(event, context):
    organization_name = event['organizationName']
    client_id = event['clientId']
    redirect_uri = event['redirectUri']
    api_key = get_api_key()

    organization = find_by_name(api_key, '/organizations', organization_name) or workos(
        api_key, 'POST', '/organizations', {'name': organization_name}
    )
    application = register_redirect_uri(api_key, client_id, redirect_uri)

    return {
        'organization_id': organization['id'],
        'application_id': application['id'],
        'client_id': application.get('client_id') or client_id,
        'redirect_uris': [item['uri'] for item in application.get('redirect_uris') or []],
    }
