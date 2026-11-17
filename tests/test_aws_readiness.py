import json
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.aws_readiness import CLIReader, ReadError, inspect_account, main, readiness_policy, save_private_json


ACCOUNT = '123456789012'
USER = 'sandbox-developer'
ARN = f'arn:aws:iam::{ACCOUNT}:user/{USER}'


class FakeReader:
    def __init__(self):
        self.calls = []
        self.values = {
            ('sts', 'get-caller-identity'): {'Account': ACCOUNT, 'Arn': ARN},
            ('iam', 'get-user'): {'User': {'Arn': ARN, 'CreateDate': '2026-01-01T00:00:00Z'}},
            ('iam', 'list-mfa-devices'): {'MFADevices': [{'SerialNumber': 'private-device-arn'}]},
            ('freetier', 'get-account-plan-state'): {'accountId': ACCOUNT, 'accountPlanType': 'PAID',
                'accountPlanStatus': 'ACTIVE', 'accountPlanRemainingCredits': 0},
            ('freetier', 'get-free-tier-usage'): {'freeTierUsages': []},
            ('dynamodb', 'list-tables'): {'TableNames': ['another-project']},
            ('dynamodb', 'describe-table'): {'Table': {'TableStatus': 'ACTIVE',
                'ProvisionedThroughput': {'ReadCapacityUnits': 12, 'WriteCapacityUnits': 8}}},
            ('lambda', 'get-account-settings'): {'AccountLimit': {'ConcurrentExecutions': 10, 'UnreservedConcurrentExecutions': 10},
                                                  'AccountUsage': {'FunctionCount': 2}},
        }

    def __call__(self, service, operation, *args):
        self.calls.append((service, operation, args))
        value = self.values[(service, operation)]
        if isinstance(value, Exception):
            raise value
        return value


class ReadinessTests(unittest.TestCase):
    def inspect(self, reader):
        return inspect_account(reader, ACCOUNT, USER, 'us-east-1')

    def test_rejects_root_wrong_user_and_wrong_account_before_other_reads(self):
        for account, arn in [(ACCOUNT, f'arn:aws:iam::{ACCOUNT}:root'),
                             (ACCOUNT, ARN + '-other'), ('999999999999', ARN)]:
            with self.subTest(arn=arn):
                read = FakeReader()
                read.values[('sts', 'get-caller-identity')] = {'Account': account, 'Arn': arn}
                with self.assertRaises(ValueError):
                    self.inspect(read)
                self.assertEqual(len(read.calls), 1)

    def test_success_collects_capacity_but_never_approves_deployment(self):
        report = self.inspect(FakeReader())
        self.assertFalse(report['deployment_ready'])
        self.assertTrue(report['checks']['mfa']['data']['registered'])
        self.assertNotIn('private-device-arn', json.dumps(report))
        capacity = report['checks']['regional_tables']['data']
        self.assertEqual(capacity['provisioned_read_capacity'], 12)
        self.assertEqual(capacity['minimal_demo_additional_read_capacity'], 15)
        self.assertEqual(report['checks']['free_tier_usage']['data']['offers'], [])

    def test_denied_mfa_is_unknown_instead_of_disabled_or_ready(self):
        read = FakeReader()
        read.values[('iam', 'list-mfa-devices')] = ReadError('AccessDenied')
        report = self.inspect(read)
        self.assertEqual(report['checks']['mfa'], {'status': 'unknown', 'error_code': 'AccessDenied'})
        self.assertEqual(report['checks']['regional_tables']['status'], 'read')
        self.assertFalse(report['deployment_ready'])

    def test_truncated_or_transitioning_tables_do_not_report_complete_capacity(self):
        for truncated in [True, False]:
            read = FakeReader()
            if truncated:
                read.values[('dynamodb', 'list-tables')]['NextToken'] = 'more'
            else:
                read.values[('dynamodb', 'describe-table')]['Table']['TableStatus'] = 'UPDATING'
            capacity = self.inspect(read)['checks']['regional_tables']['data']
            self.assertFalse(capacity['complete'])
            self.assertIsNone(capacity['provisioned_read_capacity'])
            self.assertIsNone(capacity['provisioned_write_capacity'])

    def test_truncated_free_tier_results_are_incomplete(self):
        read = FakeReader()
        read.values[('freetier', 'get-free-tier-usage')]['NextToken'] = 'more'
        self.assertFalse(self.inspect(read)['checks']['free_tier_usage']['data']['complete'])

    def test_small_lambda_quota_does_not_pass_active_demo_capacity(self):
        capacity = self.inspect(FakeReader())['checks']['lambda_capacity']['data']
        self.assertEqual(capacity['additional_reservable_capacity'], 0)
        self.assertFalse(capacity['active_demo_reservations_fit'])
        read = FakeReader()
        read.values[('lambda', 'get-account-settings')]['AccountLimit']['UnreservedConcurrentExecutions'] = 105
        capacity = self.inspect(read)['checks']['lambda_capacity']['data']
        self.assertEqual(capacity['additional_reservable_capacity'], 5)
        self.assertTrue(capacity['active_demo_reservations_fit'])

    def test_optional_audit_capture_requires_one_more_reserved_unit(self):
        read = FakeReader()
        limits = read.values[('lambda', 'get-account-settings')]['AccountLimit']
        limits['UnreservedConcurrentExecutions'] = 105
        report = inspect_account(read, ACCOUNT, USER, 'us-east-1', audit_logging_enabled=True)
        capacity = report['checks']['lambda_capacity']['data']
        self.assertEqual(capacity['active_demo_reservation_required'], 6)
        self.assertEqual(capacity['audit_archive_reservation_required'], 1)
        self.assertFalse(capacity['active_demo_reservations_fit'])
        self.assertFalse(report['deployment_ready'])
        limits['UnreservedConcurrentExecutions'] = 106
        report = inspect_account(read, ACCOUNT, USER, 'us-east-1', audit_logging_enabled=True)
        self.assertTrue(report['checks']['lambda_capacity']['data']['active_demo_reservations_fit'])
        self.assertFalse(report['deployment_ready'])

    def test_invalid_service_response_is_unknown_and_other_reads_continue(self):
        read = FakeReader()
        read.values[('freetier', 'get-account-plan-state')] = {'accountId': '999999999999'}
        report = self.inspect(read)
        self.assertEqual(report['checks']['account_plan']['status'], 'unknown')
        self.assertEqual(report['checks']['lambda_capacity']['status'], 'read')

    def test_malformed_mfa_and_missing_capacity_are_not_positive_evidence(self):
        read = FakeReader()
        read.values[('iam', 'list-mfa-devices')] = {'MFADevices': 'not-a-list'}
        del read.values[('dynamodb', 'describe-table')]['Table']['ProvisionedThroughput']
        report = self.inspect(read)
        self.assertEqual(report['checks']['mfa']['status'], 'unknown')
        self.assertEqual(report['checks']['regional_tables']['status'], 'unknown')

    def test_read_only_policy_does_not_grant_deployment_or_other_user_access(self):
        statements = readiness_policy(ACCOUNT, USER, 'us-east-1')['Statement']
        actions = {action for statement in statements for action in statement['Action']}
        self.assertEqual(actions, {'iam:GetUser', 'iam:ListMFADevices',
            'freetier:GetAccountPlanState', 'freetier:GetFreeTierUsage',
            'dynamodb:ListTables', 'dynamodb:DescribeTable', 'lambda:GetAccountSettings'})
        self.assertEqual(statements[0]['Resource'], ARN)
        self.assertNotIn('ce:GetCostAndUsage', actions)

    def test_cli_rejects_writes_and_sanitizes_errors(self):
        read = CLIReader('aws', 'sandbox', 'us-east-1')
        with patch('tools.aws_readiness.subprocess.run') as run:
            with self.assertRaises(ValueError):
                read('iam', 'attach-user-policy')
            run.assert_not_called()
            run.return_value = subprocess.CompletedProcess([], 1, '',
                'An error occurred (AccessDenied) secret-token-and-account-details')
            with self.assertRaisesRegex(ReadError, '^AccessDenied$'):
                read('iam', 'get-user', '--user-name', USER)
            command = run.call_args.args[0]
            self.assertIn('--no-cli-pager', command)
            self.assertNotIn('shell', run.call_args.kwargs)

    def test_private_output_replaces_preexisting_public_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'review.json'
            path.write_text('old')
            os.chmod(path, 0o644)
            save_private_json(path, {'deployment_ready': False})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text()), {'deployment_ready': False})

    def test_expired_cli_session_has_a_specific_safe_error(self):
        read = CLIReader('aws', 'sandbox', 'us-east-1')
        with patch('tools.aws_readiness.subprocess.run', return_value=subprocess.CompletedProcess(
                [], 255, '', "Your session has expired. Please reauthenticate using 'aws login'.")):
            with self.assertRaisesRegex(ReadError, '^ExpiredLoginSession$'):
                read('sts', 'get-caller-identity')

    def test_expired_login_handoff_does_not_replace_existing_evidence(self):
        error_output = io.StringIO()
        with patch('sys.argv', ['aws_readiness', 'check', '--account-id', ACCOUNT]), \
             patch('tools.aws_readiness.CLIReader') as reader, \
             patch('tools.aws_readiness.save_private_json') as save, \
             patch('sys.stderr', error_output):
            reader.return_value.side_effect = ReadError('ExpiredLoginSession')
            with self.assertRaises(SystemExit) as stopped:
                main()
            self.assertEqual(stopped.exception.code, 2)
            save.assert_not_called()
        self.assertIn('aws login', error_output.getvalue())
        self.assertNotIn('Traceback', error_output.getvalue())


if __name__ == '__main__':
    unittest.main()
