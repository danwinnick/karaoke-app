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


def handler(event, context):
    organization_name = event['organizationName']
    application_name = event['applicationName']
    redirect_uri = event['redirectUri']
    api_key = get_api_key()

    organization = find_by_name(api_key, '/organizations', organization_name) or workos(
        api_key, 'POST', '/organizations', {'name': organization_name}
    )

    redirect_uris = [{'uri': redirect_uri, 'default': True}]
    existing_app = find_by_name(api_key, '/connect/applications', application_name)
    if existing_app:
        application = workos(api_key, 'PUT', f"/connect/applications/{existing_app['id']}", {
            'redirect_uris': redirect_uris,
        })
    else:
        application = workos(api_key, 'POST', '/connect/applications', {
            'name': application_name,
            'description': 'Karaoke queue for DJs and singers',
            'application_type': 'oauth',
            'is_first_party': True,
            'uses_pkce': True,
            'redirect_uris': redirect_uris,
        })

    return {
        'organization_id': organization['id'],
        'application_id': application['id'],
        'client_id': application.get('client_id'),
    }
