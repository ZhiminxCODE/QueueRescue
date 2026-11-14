"""Explicit AWS sandbox operations; no credentials are used until a command runs.

login uses Cognito authorization-code + PKCE in the user's browser. Tokens stay
in an ignored, mode-0600 local file. seed uses an existing temporary AWS profile.
"""
import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid
import webbrowser


def pkce(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')


def read_deployment(path):
    config = json.loads(Path(path).read_text())
    if not re.fullmatch(r'\d{12}', config.get('account_id', '')):
        raise ValueError('Deployment must identify the target AWS account')
    region = config.get('region', '')
    if not re.fullmatch(r'https://[a-z0-9]+\.execute-api\.' + re.escape(region) + r'\.amazonaws\.com', config.get('api_url', '')):
        raise ValueError('API URL must be the deployed regional HTTPS API Gateway endpoint')
    if not re.fullmatch(r'https://[a-z0-9-]+\.auth\.' + re.escape(region) + r'\.amazoncognito\.com', config.get('cognito_login_url', '')):
        raise ValueError('Login URL must be the deployed regional HTTPS Cognito domain')
    return config


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def save_token(path, token):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(token, stream)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def login(config, token_path, no_browser=False):
    verifier, state = secrets.token_urlsafe(64), secrets.token_urlsafe(32)
    callback = 'http://localhost:8765/callback'
    result = {}
    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            parsed = urlparse(self.path)
            values = parse_qs(parsed.query)
            valid = parsed.path == '/callback' and values.get('state') == [state] and len(values.get('code', [])) == 1
            if valid:
                result['code'] = values['code'][0]
            self.send_response(200 if valid else 400)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Sign-in received. You can close this tab.' if valid else b'Invalid sign-in callback.')

    params = {'client_id':config['cognito_client_id'],'response_type':'code','scope':'openid email queuerescue/access',
              'redirect_uri':callback,'state':state,'code_challenge_method':'S256','code_challenge':pkce(verifier)}
    url = config['cognito_login_url'] + '/oauth2/authorize?' + urlencode(params)
    with HTTPServer(('127.0.0.1', 8765), Callback) as server:
        server.timeout = 1
        print('Open this Cognito URL to sign in:', url)
        if not no_browser:
            webbrowser.open(url)
        deadline = time.monotonic() + 180
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if not result:
        raise TimeoutError('Cognito login expired; no token was saved')
    body = urlencode({'grant_type':'authorization_code','client_id':config['cognito_client_id'],
                      'redirect_uri':callback,'code':result['code'],'code_verifier':verifier}).encode()
    request = Request(config['cognito_login_url'] + '/oauth2/token', data=body,
                      headers={'Content-Type':'application/x-www-form-urlencoded'})
    with build_opener(NoRedirect).open(request, timeout=10) as response:
        tokens = json.load(response)
    save_token(token_path, {'access_token':tokens['access_token'],'expires_at':int(time.time()) + int(tokens['expires_in']),
                           'api_url':config['api_url']})
    print('Access token saved locally. It was not printed or added to Git.')


def api_call(config, token_path, method, path, data=None, key=None, opener=None, sleep=time.sleep, query=None):
    if method not in {'GET','POST'} or not path.startswith('/') or path.startswith('//') or '?' in path:
        raise ValueError('Use GET/POST and a relative API path; query parameters belong in --query')
    token = json.loads(Path(token_path).read_text())
    if token['api_url'] != config['api_url'] or token['expires_at'] <= time.time() + 5:
        raise ValueError('Login is expired or belongs to another deployment; sign in again')
    headers = {'Authorization':'Bearer ' + token['access_token']}
    if method == 'POST':
        headers.update({'Content-Type':'application/json','Idempotency-Key':key or str(uuid.uuid4())})
    body = json.dumps(data or {}).encode() if method == 'POST' else None
    suffix = '?' + urlencode(query) if query else ''
    request = Request(config['api_url'] + '/api' + path + suffix, data=body, method=method, headers=headers)
    opener = opener or build_opener(NoRedirect)
    # A single prepared request retains the same key across transport retries.
    for attempt in range(3):
        try:
            with opener.open(request, timeout=35) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code not in {429,502,503,504} or attempt == 2:
                raise RuntimeError('API request failed with HTTP ' + str(error.code) + '; retry key: ' + headers.get('Idempotency-Key', '(read)')) from error
        except (URLError, TimeoutError):
            if attempt == 2:
                raise RuntimeError('API response unavailable; reuse Idempotency-Key ' + headers.get('Idempotency-Key', '(read)'))
        sleep(.5 * 2 ** attempt)


def seed(config, profile, count):
    import boto3
    from cloud.aws import wire
    if not 1 <= count <= 1000:
        raise ValueError('Seed count must be between 1 and 1000')
    session = boto3.Session(profile_name=profile, region_name=config['region'])
    caller = session.client('sts').get_caller_identity()
    if caller['Account'] != config['account_id'] or caller['Arn'].endswith(':root'):
        raise ValueError('Use a non-root identity in the reviewed development account')
    ddb, sqs = session.client('dynamodb'), session.client('sqs')
    run = uuid.uuid4().hex[:12]
    for i in range(count):
        order = 'QR-' + run + '-' + str(i)
        operation = 'shipment:' + order
        status = 'UNKNOWN' if i % 10 == 0 else 'NOT_STARTED'
        ddb.put_item(TableName=config['business_table'], Item=wire({'pk':'OP#' + operation,'sk':'META','status':status}), ConditionExpression='attribute_not_exists(pk)')
        payload = {'eventId':'evt-' + order,'operationId':operation,'eventType':'SHIP_ORDER','schemaVersion':1,'orderId':order,'warehouseId':'NY-02'}
        sqs.send_message(QueueUrl=config['source_queue_url'], MessageBody=json.dumps(payload),
            MessageAttributes={'qrDemoMode':{'DataType':'String','StringValue':'TIMEOUT_AFTER' if i % 10 == 1 else 'TIMEOUT_BEFORE'}})
    print('Seeded', count, 'synthetic source events for run', run)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment', default='.data/aws-deployment.json')
    parser.add_argument('--token-file', default='.data/aws-token.json')
    sub = parser.add_subparsers(dest='command', required=True)
    sign_in = sub.add_parser('login')
    sign_in.add_argument('--no-browser', action='store_true')
    call = sub.add_parser('call')
    call.add_argument('method', choices=['GET','POST'])
    call.add_argument('path')
    call.add_argument('--body', help='JSON body file; never put tokens in this file')
    call.add_argument('--key', help='Reuse this key when retrying an ambiguous mutation')
    call.add_argument('--query', default='{}', help='JSON object of query parameters, e.g. limit/cursor')
    generate = sub.add_parser('seed')
    generate.add_argument('--profile', default='queuerescue-dev')
    generate.add_argument('--count', type=int, default=20)
    args = parser.parse_args()
    config = read_deployment(args.deployment)
    if args.command == 'login':
        login(config, args.token_file, args.no_browser)
    elif args.command == 'seed':
        seed(config, args.profile, args.count)
    else:
        data = json.loads(Path(args.body).read_text()) if args.body else None
        print(json.dumps(api_call(config, args.token_file, args.method, args.path, data, args.key, query=json.loads(args.query)), indent=2))


if __name__ == '__main__':
    main()
