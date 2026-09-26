import base64
import json
from decimal import Decimal
from urllib.parse import quote, unquote


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _default(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f'{type(value).__name__} is not JSON serializable')


def json_response(status, data, cookies=None):
    res = {
        'statusCode': status,
        'headers': {'content-type': 'application/json', 'cache-control': 'no-store'},
        'body': json.dumps(data, default=_default),
    }
    if cookies:
        res['cookies'] = cookies
    return res


def redirect(location, cookies=None):
    res = {'statusCode': 302, 'headers': {'location': location, 'cache-control': 'no-store'}}
    if cookies:
        res['cookies'] = cookies
    return res


def parse_cookies(event):
    raw = event.get('cookies')
    if raw is None:
        header = (event.get('headers') or {}).get('cookie')
        raw = header.split(';') if header else []
    out = {}
    for c in raw:
        i = c.find('=')
        if i > 0:
            out[c[:i].strip()] = unquote(c[i + 1 :].strip())
    return out


def set_cookie(name, value, max_age_seconds):
    return f"{name}={quote(value, safe='')}; Path=/; Max-Age={max_age_seconds}; HttpOnly; Secure; SameSite=Lax"


def clear_cookie(name):
    return f'{name}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax'


def parse_body(event):
    body = event.get('body')
    if not body:
        return {}
    try:
        text = base64.b64decode(body).decode('utf-8') if event.get('isBase64Encoded') else body
        data = json.loads(text)
    except ValueError:
        raise HttpError(400, 'Invalid JSON body')
    return data if isinstance(data, dict) else {}


def require_string(value, field, max_len=500):
    if not isinstance(value, str) or not value.strip():
        raise HttpError(400, f'{field} is required')
    if len(value) > max_len:
        raise HttpError(400, f'{field} is too long')
    return value.strip()
