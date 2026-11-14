import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from urllib.error import HTTPError

from tools.aws_console import api_call, pkce, read_deployment, save_token


class AwsConsoleTests(unittest.TestCase):
    def config(self):
        return {'account_id':'123456789012','region':'us-east-1','api_url':'https://example123.execute-api.us-east-1.amazonaws.com',
                'cognito_login_url':'https://queuerescue-dev-123456789012.auth.us-east-1.amazoncognito.com'}

    def test_pkce_matches_rfc7636_example(self):
        self.assertEqual(pkce('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'), 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM')

    def test_tokens_are_private_and_bound_to_a_deployment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'token.json'
            save_token(path, {'access_token':'test-only','api_url':'https://other.example','expires_at':int(time.time()) + 100})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                api_call(self.config(), path, 'GET', '/messages')

    def test_authentication_is_never_sent_to_an_arbitrary_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'deployment.json'
            for url in ['http://example123.execute-api.us-east-1.amazonaws.com', 'https://untrusted.example']:
                path.write_text(json.dumps({**self.config(),'api_url':url}))
                with self.assertRaises(ValueError):
                    read_deployment(path)

    def test_network_retries_reuse_one_idempotency_key(self):
        calls = []
        class Opener:
            def open(self, request, timeout):
                calls.append(request.get_header('Idempotency-key'))
                if len(calls) == 1:
                    raise HTTPError(request.full_url, 503, 'unavailable', {}, io.BytesIO())
                return io.BytesIO(b'{"id":"one-job"}')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'token.json'
            save_token(path, {'access_token':'test-only','api_url':self.config()['api_url'],'expires_at':int(time.time()) + 100})
            result = api_call(self.config(), path, 'POST', '/replay-jobs', {}, opener=Opener(), sleep=lambda seconds: None)
            self.assertEqual(result['id'], 'one-job')
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0], calls[1])
            self.assertTrue(calls[0])
