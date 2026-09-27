import hashlib
import hmac
import re
import secrets

_ses = None

EMAIL_RE = re.compile(r'[^\s@]+@[^\s@]+\.[^\s@]+')


def normalize_email(value):
    email = value.strip().lower() if isinstance(value, str) else ''
    return email if len(email) <= 254 and EMAIL_RE.fullmatch(email) else None


def new_code():
    return f'{secrets.randbelow(1_000_000):06d}'


# Only a keyed hash of the code is stored, so a table read alone can't be used to log in.
def hash_code(secret, email, code):
    return hmac.new(secret.encode(), f'{email}:{code}'.encode(), hashlib.sha256).hexdigest()


def code_matches(secret, email, code, code_hash):
    return isinstance(code_hash, str) and hmac.compare_digest(hash_code(secret, email, code), code_hash)


def send_code(sender, email, code, minutes):
    global _ses
    if _ses is None:
        import boto3  # imported lazily so the helpers above can be unit tested without boto3

        _ses = boto3.client('sesv2')
    text = (
        f'Your Karaoke DJ login code is {code}\n\n'
        f'It expires in {minutes} minutes. If you did not try to log in, you can ignore this email.\n'
    )
    _ses.send_email(
        FromEmailAddress=sender,
        Destination={'ToAddresses': [email]},
        Content={
            'Simple': {
                'Subject': {'Data': f'{code} is your Karaoke login code'},
                'Body': {'Text': {'Data': text}},
            }
        },
    )
