import base64
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))

from lib.login_code import code_matches, hash_code, new_code, normalize_email  # noqa: E402
from lib.night import night_date  # noqa: E402
from lib.session import sign_token, verify_token  # noqa: E402
from lib.youtube import clean_title  # noqa: E402


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


class NightTest(unittest.TestCase):
    def test_rolls_over_at_6am_local_time(self):
        # 1am PDT Saturday -> Friday night
        self.assertEqual(night_date(utc('2026-06-06T08:00:00'), 'America/Los_Angeles'), '2026-06-05')
        # 7am PDT Saturday -> Saturday
        self.assertEqual(night_date(utc('2026-06-06T14:00:00'), 'America/Los_Angeles'), '2026-06-06')
        # 11pm PDT Friday -> Friday
        self.assertEqual(night_date(utc('2026-06-06T06:00:00'), 'America/Los_Angeles'), '2026-06-05')


class SessionTest(unittest.TestCase):
    def test_round_trip_and_reject_tampering(self):
        token = sign_token({'sub': 'user_1', 'role': 'dj'}, 'secret', 60)
        self.assertEqual(verify_token(token, 'secret')['sub'], 'user_1')
        self.assertIsNone(verify_token(token, 'other'))
        body, sig = token.split('.')
        forged = base64.urlsafe_b64encode(json.dumps({'sub': 'user_2', 'role': 'dj', 'exp': 9e9}).encode())
        self.assertIsNone(verify_token(f"{forged.decode().rstrip('=')}.{sig}", 'secret'))
        self.assertIsNone(verify_token(f'{body}.', 'secret'))
        self.assertIsNone(verify_token(sign_token({'sub': 'x'}, 'secret', -1), 'secret'))


class LoginCodeTest(unittest.TestCase):
    def test_codes_are_six_digits(self):
        for _ in range(50):
            self.assertRegex(new_code(), r'^[0-9]{6}$')

    def test_hash_is_bound_to_email_and_secret(self):
        code_hash = hash_code('secret', 'dj@example.com', '123456')
        self.assertTrue(code_matches('secret', 'dj@example.com', '123456', code_hash))
        self.assertFalse(code_matches('secret', 'dj@example.com', '123457', code_hash))
        self.assertFalse(code_matches('secret', 'other@example.com', '123456', code_hash))
        self.assertFalse(code_matches('other', 'dj@example.com', '123456', code_hash))
        self.assertFalse(code_matches('secret', 'dj@example.com', '123456', None))

    def test_normalize_email(self):
        self.assertEqual(normalize_email('  DJ@Example.COM '), 'dj@example.com')
        self.assertIsNone(normalize_email('not-an-email'))
        self.assertIsNone(normalize_email(None))
        self.assertIsNone(normalize_email('a@b.c' + 'x' * 260))


class CleanTitleTest(unittest.TestCase):
    def test_strips_karaoke_noise(self):
        self.assertEqual(clean_title('Adele - Someone Like You (Karaoke Version) | Sing King'), 'Adele - Someone Like You')
        self.assertEqual(clean_title('Queen - Bohemian Rhapsody [Karaoke with Lyrics]'), 'Queen - Bohemian Rhapsody')
        self.assertEqual(clean_title('Don&#39;t Stop Believin&#39; - Journey (Karaoke)'), "Don't Stop Believin' - Journey")
        self.assertEqual(clean_title('Karaoke - Toxic - Britney Spears'), 'Toxic - Britney Spears')
        self.assertEqual(clean_title('Wonderwall (Remastered)'), 'Wonderwall (Remastered)')


if __name__ == '__main__':
    unittest.main()
