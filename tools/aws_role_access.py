"""Prepare minimal-stack IAM review documents offline; never grants access.

The administrator owns the boundary. The IAM fragment is only one part of a
future deployment policy, not permission to deploy or approval to incur costs.
"""
import argparse
import re

from tools.aws_readiness import save_private_json


def role_access_proposal(account_id, region, environment):
    if not re.fullmatch(r'[0-9]{12}', account_id):
        raise ValueError('Use the reviewed 12-digit development account ID')
    if not re.fullmatch(r'[a-z]{2}-[a-z]+-[0-9]+', region):
        raise ValueError('Use a commercial AWS region')
    if environment not in ('dev', 'test'):
        raise ValueError('Only dev and test environments are supported')
    prefix = f'queuerescue-{environment}'
    regional = f'arn:aws:{{service}}:{region}:{account_id}:'
    iam = f'arn:aws:iam::{account_id}:'
    names = ['api', 'collector', 'replay', 'consumer', 'verifier']
    roles = [iam + f'role/{prefix}-{name}' for name in names]
    workflow_role = iam + f'role/{prefix}-workflow'
    boundary_arn = iam + f'policy/{prefix}-execution-boundary'
    table = regional.format(service='dynamodb') + f'table/{prefix}-'
    queue = regional.format(service='sqs') + prefix + '-'
    evidence = f'arn:aws:s3:::{prefix}-{account_id}-{region}-evidence/evidence/*'
    workflow = regional.format(service='states') + f'stateMachine:{prefix}-recovery'

    def statement(sid, actions, resources, condition=None, effect='Allow'):
        value = {'Sid': sid, 'Effect': effect, 'Action': actions, 'Resource': resources}
        if condition:
            value['Condition'] = condition
        return value

    def policy(statements):
        return {'Version': '2012-10-17', 'Statement': statements}

    boundary = policy([
        statement('Ledger', ['dynamodb:GetItem', 'dynamodb:Query', 'dynamodb:PutItem', 'dynamodb:UpdateItem', 'dynamodb:DeleteItem', 'dynamodb:ConditionCheckItem'], [table + 'ledger']),
        statement('Business', ['dynamodb:GetItem', 'dynamodb:UpdateItem'], [table + 'business']),
        statement('AuditAppendAndRead', ['dynamodb:GetItem', 'dynamodb:Query', 'dynamodb:PutItem'], [table + 'audit']),
        statement('Evidence', ['s3:GetObject', 's3:PutObject'], [evidence], {'StringEquals': {'s3:ResourceAccount': account_id}}),
        statement('CollectWithoutDelete', ['sqs:ReceiveMessage', 'sqs:ChangeMessageVisibility', 'sqs:GetQueueAttributes'], [queue + 'source-dlq']),
        statement('Consume', ['sqs:ReceiveMessage', 'sqs:DeleteMessage', 'sqs:GetQueueAttributes'], [queue + 'source', queue + 'replay']),
        statement('ReplayDestinationOnly', ['sqs:SendMessage'], [queue + 'replay']),
        statement('FunctionLogs', ['logs:CreateLogStream', 'logs:PutLogEvents'], [regional.format(service='logs') + f'log-group:/aws/lambda/{prefix}-{name}:*' for name in names]),
        statement('ScopedEvidenceLookup', ['logs:FilterLogEvents'], [regional.format(service='logs') + f'log-group:/aws/lambda/{prefix}-{name}:*' for name in ['consumer', 'replay']]),
        statement('InternalInvocation', ['lambda:InvokeFunction'], [regional.format(service='lambda') + f'function:{prefix}-{name}' for name in ['collector', 'verifier', 'replay']]),
        statement('RecoveryWorkflow', ['states:StartExecution'], [workflow]),
    ])
    all_roles = roles + [workflow_role]
    approved = {'StringEquals': {'iam:PermissionsBoundary': boundary_arn}}
    delegation = policy([
        statement('CreateOnlyBoundedRoles', ['iam:CreateRole', 'iam:PutRolePermissionsBoundary'], all_roles, approved),
        statement('RejectMissingOrDifferentBoundary', ['iam:CreateRole', 'iam:PutRolePermissionsBoundary'], all_roles, {'StringNotEquals': {'iam:PermissionsBoundary': boundary_arn}}, 'Deny'),
        statement('MaintainExactRoles', ['iam:GetRole', 'iam:ListRolePolicies', 'iam:GetRolePolicy', 'iam:ListAttachedRolePolicies', 'iam:TagRole', 'iam:UntagRole', 'iam:UpdateAssumeRolePolicy', 'iam:PutRolePolicy', 'iam:DeleteRolePolicy'], all_roles),
        statement('PassLambdaRoles', ['iam:PassRole'], roles, {'StringEquals': {'iam:PassedToService': 'lambda.amazonaws.com'}}),
        statement('PassWorkflowRole', ['iam:PassRole'], [workflow_role], {'StringEquals': {'iam:PassedToService': 'states.amazonaws.com'}}),
        statement('KeepRoleBoundary', ['iam:DeleteRolePermissionsBoundary'], all_roles, effect='Deny'),
        statement('KeepAdministratorOwnedBoundary', ['iam:CreatePolicyVersion', 'iam:SetDefaultPolicyVersion', 'iam:DeletePolicyVersion', 'iam:DeletePolicy'], [boundary_arn], effect='Deny'),
    ])
    return {'scope': 'Minimal base stack only; IAM delegation fragment, not complete deployment access',
            'deployment_ready': False, 'boundary_arn': boundary_arn, 'role_arns': all_roles,
            'runtime_boundary': boundary, 'iam_delegation_fragment': delegation,
            'review_requirements': [
                'Administrator must review and create the boundary before any delegation grant.',
                'Verify all existing exact-name roles already have this boundary before granting maintenance access; name collisions are not proof of ownership.',
                'Review all effective deployer policies, role trust policies and resource policies. This fragment does not constrain unrelated grants.',
                'The shared boundary is a ceiling; per-role inline policies retain the narrower runtime permissions. A deployer can change code/inline policies within that ceiling and is trusted with project data.',
                'This boundary excludes the standard KMS profile and optional audit archive. Review separate ceilings/delegation before using either.',
                'IAM policy reads for the boundary, service provisioning permissions and reviewed teardown access are not granted by this fragment.',
                'No real AWS simulation or authorization test has been performed; inspect service acceptance and effective access before deployment.',
                'Cost eligibility, quotas and exact deployment/retention approval remain separate prerequisites.',
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account-id', required=True)
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--environment', choices=['dev', 'test'], default='dev')
    args = parser.parse_args()
    try:
        proposal = role_access_proposal(args.account_id, args.region, args.environment)
    except ValueError as error:
        parser.error(str(error))
    save_private_json('.data/aws-role-access.json', proposal)
    print('Saved private proposal to .data/aws-role-access.json. No AWS calls or permission changes. Review required.')


if __name__ == '__main__':
    main()
