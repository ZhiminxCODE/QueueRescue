"""Read-only account readiness evidence; never deploys or grants permissions.

The policy command is entirely offline. The check command uses an existing AWS
CLI temporary-credential profile and saves account details only to a local file.
Neither a successful check nor a Free Tier offer is a guarantee of zero charges.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


READ_OPERATIONS = {
    ('sts', 'get-caller-identity'), ('iam', 'get-user'),
    ('iam', 'list-mfa-devices'), ('freetier', 'get-account-plan-state'),
    ('freetier', 'get-free-tier-usage'), ('dynamodb', 'list-tables'),
    ('dynamodb', 'describe-table'), ('lambda', 'get-account-settings'),
}


def validate_target(account_id, user_name, region):
    if not re.fullmatch(r'\d{12}', account_id):
        raise ValueError('Use the reviewed 12-digit account ID')
    if not re.fullmatch(r'[A-Za-z0-9_+=,.@-]{1,64}', user_name):
        raise ValueError('Use an IAM user name without a path')
    if not re.fullmatch(r'[a-z]{2}-[a-z]+-\d', region):
        raise ValueError('Use a commercial AWS region')


def readiness_policy(account_id, user_name, region):
    validate_target(account_id, user_name, region)
    return {'Version': '2012-10-17', 'Statement': [
        {'Sid': 'ReadOwnIdentityAndMFA', 'Effect': 'Allow',
         'Action': ['iam:GetUser', 'iam:ListMFADevices'],
         'Resource': f'arn:aws:iam::{account_id}:user/{user_name}'},
        # These APIs do not support resource-level ARN restrictions.
        {'Sid': 'ReadFreeTierEligibility', 'Effect': 'Allow',
         'Action': ['freetier:GetAccountPlanState', 'freetier:GetFreeTierUsage'],
         'Resource': '*'},
        {'Sid': 'ReadRegionalCapacity', 'Effect': 'Allow',
         'Action': ['dynamodb:ListTables', 'lambda:GetAccountSettings'],
         'Resource': '*',
         'Condition': {'StringEquals': {'aws:RequestedRegion': region}}},
        {'Sid': 'ReadTableCapacityOnly', 'Effect': 'Allow',
         'Action': ['dynamodb:DescribeTable'],
         'Resource': f'arn:aws:dynamodb:{region}:{account_id}:table/*'},
    ]}


def save_private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, staged = tempfile.mkstemp(prefix='.readiness-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
        os.replace(staged, path)
    finally:
        if os.path.exists(staged):
            os.unlink(staged)


class ReadError(RuntimeError):
    pass


class CLIReader:
    def __init__(self, binary, profile, region):
        self.binary, self.profile, self.region = binary, profile, region

    def __call__(self, service, operation, *arguments):
        if (service, operation) not in READ_OPERATIONS:
            raise ValueError('Operation is not in the read-only readiness allowlist')
        command = [self.binary, service, operation, *arguments, '--profile', self.profile,
                   '--region', self.region, '--output', 'json', '--no-cli-pager',
                   '--cli-connect-timeout', '5', '--cli-read-timeout', '10']
        env = dict(os.environ, AWS_PAGER='', AWS_CLI_AUTO_PROMPT='off')
        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=30, env=env, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise ReadError('CLIUnavailableOrTimedOut') from None
        if result.returncode:
            # Record only the AWS error code, never raw stderr or credentials.
            if re.search(r'session (?:has )?expired', result.stderr, re.IGNORECASE):
                raise ReadError('ExpiredLoginSession')
            match = re.search(r'\(([A-Za-z0-9_.-]+)\)', result.stderr)
            raise ReadError(match.group(1) if match else 'CLIRequestFailed')
        try:
            value = json.loads(result.stdout)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except ValueError:
            raise ReadError('InvalidCLIResponse') from None


def inspect_account(read, account_id, user_name, region):
    validate_target(account_id, user_name, region)
    caller = read('sts', 'get-caller-identity')
    expected_arn = f'arn:aws:iam::{account_id}:user/{user_name}'
    if caller.get('Account') != account_id or caller.get('Arn') != expected_arn:
        raise ValueError('Profile must use the exact reviewed non-root IAM user and account')
    report = {
        'reviewed_at': datetime.now(timezone.utc).isoformat(),
        'account_id': account_id, 'user_arn': expected_arn, 'region': region,
        'checks': {}, 'deployment_ready': False,
        'review_notes': [
            'Manual cost and permission review is required; this tool never authorizes deployment.',
            'Missing Free Tier usage entries do not prove that a service is free.',
            'API Gateway legacy free usage expires 12 months after account signup.',
            'Registered MFA does not prove this existing CLI session was authenticated with MFA.',
            'Regional table totals do not establish all account-wide Free Tier usage.',
        ],
    }

    def check(name, operation):
        try:
            report['checks'][name] = {'status': 'read', 'data': operation()}
        except ReadError as error:
            report['checks'][name] = {'status': 'unknown', 'error_code': str(error)}
        except (KeyError, TypeError, ValueError):
            report['checks'][name] = {'status': 'unknown', 'error_code': 'InvalidServiceResponse'}

    def identity():
        user = read('iam', 'get-user', '--user-name', user_name)['User']
        if user['Arn'] != expected_arn:
            raise ValueError()
        return {'arn': user['Arn'], 'user_created_at': user['CreateDate']}

    def mfa():
        devices = read('iam', 'list-mfa-devices', '--user-name', user_name)['MFADevices']
        if not isinstance(devices, list):
            raise ValueError()
        return {'registered': bool(devices), 'device_count': len(devices)}

    def plan():
        response = read('freetier', 'get-account-plan-state')
        if response['accountId'] != account_id or not response['accountPlanType'] or not response['accountPlanStatus']:
            raise ValueError()
        return {key: response[key] for key in ('accountPlanType', 'accountPlanStatus',
                'accountPlanRemainingCredits', 'accountPlanExpirationDate') if key in response}

    def offers():
        # CLI pagination is bounded. A truncated result remains incomplete.
        response = read('freetier', 'get-free-tier-usage', '--max-items', '100')
        if not isinstance(response['freeTierUsages'], list):
            raise ValueError()
        return {'offers': response['freeTierUsages'],
                'complete': not bool(response.get('NextToken') or response.get('nextToken'))}

    def capacity():
        response = read('dynamodb', 'list-tables', '--max-items', '100')
        complete = not bool(response.get('NextToken') or response.get('LastEvaluatedTableName'))
        tables, reads, writes = [], 0, 0
        for name in response['TableNames']:
            table = read('dynamodb', 'describe-table', '--table-name', name)['Table']
            if table.get('TableStatus') != 'ACTIVE':
                complete = False
            mode = table.get('BillingModeSummary', {}).get('BillingMode', 'PROVISIONED')
            if mode not in {'PROVISIONED', 'PAY_PER_REQUEST'}:
                raise ValueError()
            provisioned = table.get('ProvisionedThroughput', {})
            rcu, wcu = provisioned.get('ReadCapacityUnits', 0), provisioned.get('WriteCapacityUnits', 0)
            if mode == 'PROVISIONED' and any(type(provisioned.get(key)) is not int or provisioned[key] < 0
                    for key in ('ReadCapacityUnits', 'WriteCapacityUnits')):
                raise ValueError()
            tables.append({'name': name, 'billing_mode': mode, 'read_capacity': rcu,
                           'write_capacity': wcu})
            if mode == 'PROVISIONED':
                reads += rcu
                writes += wcu
        return {'tables': tables, 'complete': complete,
                'provisioned_read_capacity': reads if complete else None,
                'provisioned_write_capacity': writes if complete else None,
                'minimal_demo_additional_read_capacity': 15,
                'minimal_demo_additional_write_capacity': 15}

    def concurrency():
        response = read('lambda', 'get-account-settings')
        return {'limits': response['AccountLimit'], 'usage': response['AccountUsage']}

    for name, operation in [('identity', identity), ('mfa', mfa), ('account_plan', plan),
                            ('free_tier_usage', offers), ('regional_tables', capacity),
                            ('lambda_capacity', concurrency)]:
        check(name, operation)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['policy', 'check'])
    parser.add_argument('--account-id', required=True)
    parser.add_argument('--user-name', default='queuerescue-dev')
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--profile', default='queuerescue-dev')
    parser.add_argument('--aws-cli', default='.data/bin/aws')
    parser.add_argument('--output', help='Private local JSON file; do not add it to Git')
    args = parser.parse_args()
    try:
        validate_target(args.account_id, args.user_name, args.region)
        if args.command == 'policy':
            path = args.output or '.data/aws-readiness-policy.json'
            value = readiness_policy(args.account_id, args.user_name, args.region)
        else:
            path = args.output or '.data/aws-readiness.json'
            value = inspect_account(CLIReader(args.aws_cli, args.profile, args.region),
                                    args.account_id, args.user_name, args.region)
    except (ReadError, ValueError) as error:
        detail = ('AWS session expired. Refresh the reviewed non-root profile with aws login.'
                  if str(error) == 'ExpiredLoginSession' else str(error))
        parser.exit(2, 'Readiness check stopped: ' + detail + '\nNo report was updated.\n')
    save_private_json(path, value)
    print('Saved private', args.command, 'output to', path)
    if args.command == 'check':
        for name, result in value['checks'].items():
            print(name + ': ' + result['status'])
        print('Manual review is still required. No permissions or resources were changed.')


if __name__ == '__main__':
    main()
