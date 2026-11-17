import json
import io
import subprocess
import sys
import unittest
from unittest.mock import patch
from fnmatch import fnmatchcase

from tools.aws_role_access import main, role_access_proposal

ACCOUNT = '123456789012'
REGION = 'us-east-1'


def allows(policy, action, resource, context=None):
    """Small evaluator for the emitted policy subset, not an IAM simulator."""
    allowed = False
    for s in policy['Statement']:
        if not any(fnmatchcase(action.lower(), a.lower()) for a in s['Action']):
            continue
        if not any(fnmatchcase(resource, r) for r in s['Resource']):
            continue
        matched = True
        for operator, conditions in s.get('Condition', {}).items():
            for key, expected in conditions.items():
                actual = (context or {}).get(key)
                matched &= (actual == expected) if operator == 'StringEquals' else (actual != expected)
        if matched:
            if s['Effect'] == 'Deny':
                return False
            allowed = True
    return allowed


class RoleAccessTests(unittest.TestCase):
    def setUp(self):
        self.bundle = role_access_proposal(ACCOUNT, REGION, 'dev')
        self.boundary = self.bundle['runtime_boundary']
        self.delegation = self.bundle['iam_delegation_fragment']
        self.role = f'arn:aws:iam::{ACCOUNT}:role/queuerescue-dev-api'
        self.context = {'iam:PermissionsBoundary': self.bundle['boundary_arn']}

    def test_proposal_is_offline_minimal_and_not_a_deployment_grant(self):
        self.assertFalse(self.bundle['deployment_ready'])
        self.assertEqual(self.bundle['scope'], 'Minimal base stack only; IAM delegation fragment, not complete deployment access')
        self.assertEqual(len(self.bundle['role_arns']), 6)
        self.assertNotIn('audit-archive', json.dumps(self.bundle))
        for policy in [self.boundary, self.delegation]:
            for statement in policy['Statement']:
                if statement['Effect'] == 'Allow':
                    self.assertNotIn('*', statement['Resource'])
                    self.assertTrue(all('*' not in a for a in statement['Action']))

    def test_role_creation_requires_the_exact_boundary(self):
        self.assertTrue(allows(self.delegation, 'iam:CreateRole', self.role, self.context))
        for context in [{}, {'iam:PermissionsBoundary': 'arn:aws:iam::999999999999:policy/other'}]:
            self.assertFalse(allows(self.delegation, 'iam:CreateRole', self.role, context))
            self.assertFalse(allows(self.delegation, 'iam:PutRolePermissionsBoundary', self.role, context))
        for role in [self.role + '-extra', self.role.replace(ACCOUNT, '999999999999'), self.role.replace('-dev-', '-test-')]:
            self.assertFalse(allows(self.delegation, 'iam:CreateRole', role, self.context))
            self.assertFalse(allows(self.delegation, 'iam:PutRolePolicy', role))

    def test_pass_role_is_bound_to_each_intended_service(self):
        for role in self.bundle['role_arns']:
            service = 'states.amazonaws.com' if role.endswith('-workflow') else 'lambda.amazonaws.com'
            self.assertTrue(allows(self.delegation, 'iam:PassRole', role, {'iam:PassedToService': service}))
            for other in ['ec2.amazonaws.com', 'states.amazonaws.com' if service.startswith('lambda') else 'lambda.amazonaws.com', None]:
                self.assertFalse(allows(self.delegation, 'iam:PassRole', role, {'iam:PassedToService': other}))

    def test_boundary_cannot_be_removed_or_edited_and_no_user_access_is_granted(self):
        for action, resource in [('iam:DeleteRolePermissionsBoundary', self.role), ('iam:CreatePolicyVersion', self.bundle['boundary_arn']), ('iam:SetDefaultPolicyVersion', self.bundle['boundary_arn']), ('iam:DeletePolicy', self.bundle['boundary_arn']), ('iam:AttachRolePolicy', self.role), ('iam:CreateAccessKey', f'arn:aws:iam::{ACCOUNT}:user/developer')]:
            self.assertFalse(allows(self.delegation, action, resource))
        # Explicit protection must win even if a later policy grants a wildcard.
        broad = {'Effect': 'Allow', 'Action': ['iam:*'], 'Resource': ['*']}
        joined = {'Statement': self.delegation['Statement'] + [broad]}
        self.assertFalse(allows(joined, 'iam:DeleteRolePermissionsBoundary', self.role))
        self.assertFalse(allows(joined, 'iam:CreatePolicyVersion', self.bundle['boundary_arn']))
        self.assertFalse(allows(joined, 'iam:PutRolePermissionsBoundary', self.role, {}))

    def test_boundary_covers_runtime_contract_without_destructive_expansion(self):
        ar = f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/queuerescue-dev-'
        bucket = f'arn:aws:s3:::queuerescue-dev-{ACCOUNT}-{REGION}-evidence'
        queue = f'arn:aws:sqs:{REGION}:{ACCOUNT}:queuerescue-dev-'
        for action, resource in [('dynamodb:ConditionCheckItem', ar+'ledger'), ('dynamodb:UpdateItem', ar+'business'), ('dynamodb:PutItem', ar+'audit'), ('s3:PutObject', bucket+'/evidence/synthetic.json'), ('sqs:ChangeMessageVisibility', queue+'source-dlq'), ('sqs:DeleteMessage', queue+'source'), ('sqs:SendMessage', queue+'replay'), ('states:StartExecution', f'arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:queuerescue-dev-recovery')]:
            context = {'s3:ResourceAccount': ACCOUNT} if action.startswith('s3:') else {}
            self.assertTrue(allows(self.boundary, action, resource, context), (action, resource))
        for action, resource in [('dynamodb:DeleteItem', ar+'audit'), ('dynamodb:UpdateItem', ar+'audit'), ('s3:DeleteObject', bucket+'/evidence/synthetic.json'), ('sqs:DeleteMessage', queue+'source-dlq'), ('sqs:SendMessage', queue+'source'), ('iam:CreateRole', self.role)]:
            self.assertFalse(allows(self.boundary, action, resource))
        for changed in [ar.replace(ACCOUNT, '999999999999')+'ledger', ar.replace(REGION, 'us-west-2')+'ledger', ar+'ledger-extra']:
            self.assertFalse(allows(self.boundary, 'dynamodb:GetItem', changed))
        self.assertFalse(allows(self.boundary, 's3:GetObject', bucket+'/evidence/x', {'s3:ResourceAccount': '999999999999'}))

    def test_invalid_targets_are_rejected(self):
        for args in [('bad', REGION, 'dev'), (ACCOUNT, 'us-east-1:*', 'dev'), (ACCOUNT, REGION, 'production')]:
            with self.assertRaises(ValueError):
                role_access_proposal(*args)

    def test_cli_does_not_accept_deployment_or_credential_options(self):
        run = subprocess.run([sys.executable, '-m', 'tools.aws_role_access', '--account-id', ACCOUNT, '--apply'], capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('unrecognized arguments', run.stderr)

    def test_test_environment_has_no_dev_role_grants(self):
        other = role_access_proposal(ACCOUNT, REGION, 'test')
        self.assertNotIn('queuerescue-dev-', json.dumps(other))
        self.assertIn('queuerescue-test-execution-boundary', other['boundary_arn'])

    def test_cli_saves_only_private_review_bundle_and_reports_no_aws_changes(self):
        with patch('sys.argv', ['aws_role_access', '--account-id', ACCOUNT]), patch('tools.aws_role_access.save_private_json') as save, patch('sys.stdout', new_callable=io.StringIO) as output:
            main()
        self.assertEqual(save.call_args.args[0], '.data/aws-role-access.json')
        self.assertFalse(save.call_args.args[1]['deployment_ready'])
        self.assertNotIn(ACCOUNT, output.getvalue())
        self.assertIn('No AWS calls', output.getvalue())

    def test_cli_rejects_invalid_account_before_writing(self):
        with patch('sys.argv', ['aws_role_access', '--account-id', 'invalid']), patch('tools.aws_role_access.save_private_json') as save, patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaises(SystemExit):
                main()
        save.assert_not_called()
