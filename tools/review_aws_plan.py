"""Check selected invariants in a full minimal/idle Terraform plan JSON offline.

This is a preflight aid, not IAM validation, a cost cap, or deployment approval.
It never calls AWS or Terraform and has no apply option.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlsplit

from tools.aws_readiness import save_private_json
from tools.aws_role_access import role_access_proposal


FUNCTIONS = ['api', 'collector', 'consumer', 'replay', 'verifier']
QUEUES = ['source', 'source-dlq', 'replay', 'replay-dlq']
SINGLETONS = [
    'aws_apigatewayv2_api.api', 'aws_apigatewayv2_authorizer.cognito', 'aws_apigatewayv2_integration.lambda',
    'aws_apigatewayv2_route.authenticated', 'aws_apigatewayv2_stage.default', 'aws_cloudwatch_log_group.api',
    'aws_cognito_resource_server.api', 'aws_cognito_user_pool.operators', 'aws_cognito_user_pool_client.dashboard',
    'aws_cognito_user_pool_domain.login', 'aws_iam_role.workflow', 'aws_iam_role_policy.workflow', 'aws_lambda_permission.api',
    'aws_s3_bucket.evidence', 'aws_s3_bucket_lifecycle_configuration.evidence', 'aws_s3_bucket_policy.evidence',
    'aws_s3_bucket_public_access_block.evidence', 'aws_s3_bucket_server_side_encryption_configuration.evidence',
    'aws_s3_bucket_versioning.evidence', 'aws_sfn_state_machine.recovery', 'aws_sns_topic.alerts',
]


def review_plan(plan, account_id, region, environment):
    role_access_proposal(account_id, region, environment)
    report = {'scope': 'Full minimal base plan, workers off; selected offline checks only',
              'checks_passed': False, 'deployment_ready': False, 'resource_count': 0, 'issues': [],
              'manual_reviews': [
                  'Verify the authenticated non-root account, current source/artifact, plan provenance and SHA-256 before applying the exact saved binary plan. This JSON report is not an approval.',
                  'Review actual IAM/trust/resource policy contents, external boundary existence/contents, generated-ID ownership and administrator bootstrap/log delivery requirements.',
                  'Review callbacks/CORS ownership, email delivery/MFA, quota, cost/credit coverage, complete retained-resource costs and destructive cleanup permissions.',
                  'Some computed attributes are unknown before apply; only the explicitly checked settings are assessed. Inspect all remaining configuration and live service acceptance separately.',
              ]}

    def issue(code, address=None):
        item = {'code': code}
        if address is not None:
            item['resource_address'] = address
        report['issues'].append(item)

    if not isinstance(plan, dict) or not isinstance(plan.get('format_version'), str) or not plan['format_version'].startswith('1.'):
        issue('PLAN_FORMAT')
        return report
    if plan.get('errored') or plan.get('complete') is False or plan.get('deferred_changes'):
        issue('PLAN_INCOMPLETE')
    if plan.get('resource_drift'):
        issue('DRIFT_REQUIRES_REVIEW')
    raw_variables = plan.get('variables', {})
    variables = {k: v.get('value') for k, v in raw_variables.items() if isinstance(v, dict)} if isinstance(raw_variables, dict) else {}
    boundary = f'arn:aws:iam::{account_id}:policy/queuerescue-{environment}-execution-boundary'
    required = {'aws_account_id': account_id, 'aws_region': region, 'environment': environment, 'cost_profile': 'minimal',
                'execution_permissions_boundary_arn': boundary, 'protect_data': True, 'allow_destroy_evidence': False,
                **dict.fromkeys(['consumers_enabled', 'replay_enabled', 'audit_logging_enabled', 'audit_collection_enabled', 'dashboard_hosting_enabled', 'dashboard_delivery_enabled'], False)}
    for key, expected in required.items():
        if type(variables.get(key)) is not type(expected) or variables[key] != expected:
            issue('VARIABLE_' + key.upper())
    email = variables.get('budget_alert_email')
    if not isinstance(email, str):
        issue('VARIABLE_BUDGET_ALERT_EMAIL')
    expected_addresses = set(SINGLETONS)
    indexed = {'aws_iam_role.function': FUNCTIONS, 'aws_iam_role_policy.function': FUNCTIONS,
               'aws_lambda_function.function': FUNCTIONS, 'aws_cloudwatch_log_group.function': FUNCTIONS,
               'aws_sqs_queue.queue': QUEUES, 'aws_sqs_queue_policy.tls': QUEUES,
               'aws_sqs_queue_redrive_policy.source': ['source', 'replay'], 'aws_sqs_queue_redrive_allow_policy.dlq': ['source', 'replay'],
               'aws_lambda_event_source_mapping.shipment': ['source', 'replay'],
               'aws_dynamodb_table.data': ['audit', 'business', 'ledger'], 'aws_cognito_user_group.role': ['Admin', 'Approver', 'Operator', 'Viewer']}
    expected_addresses.update(f'{name}["{key}"]' for name, keys in indexed.items() for key in keys)
    if email:
        expected_addresses.update(['aws_sns_topic_subscription.email[0]', 'aws_budgets_budget.sandbox[0]'])
    changes = plan.get('resource_changes')
    if not isinstance(changes, list):
        issue('PLAN_RESOURCES')
        return report
    report['resource_count'] = len(changes)
    resources = {}
    for item in changes:
        if not isinstance(item, dict) or not isinstance(item.get('address'), str):
            issue('RESOURCE_FORMAT')
            continue
        address = item['address']
        if address in resources:
            issue('DUPLICATE_RESOURCE', address)
        if item.get('provider_name') != 'registry.terraform.io/hashicorp/aws' or item.get('mode') != 'managed' or item.get('type') != address.split('.')[0]:
            issue('RESOURCE_PROVIDER', address)
        change = item.get('change')
        if not isinstance(change, dict):
            issue('RESOURCE_VALUES', address)
            continue
        if change.get('actions') not in [['create'], ['update'], ['no-op']]:
            issue('DESTRUCTIVE_OR_UNSUPPORTED_ACTION', address)
        if change.get('importing') is not None or item.get('previous_address'):
            issue('IMPORT_REQUIRES_REVIEW', address)
        after = change.get('after')
        if not isinstance(after, dict):
            issue('RESOURCE_VALUES', address)
            after = {}
        resources[address] = after
    if set(resources) != expected_addresses:
        issue('RESOURCE_INVENTORY')

    def check(address, code, predicate):
        try:
            passed = predicate(resources[address]) is True
        except (KeyError, TypeError, ValueError, IndexError):
            passed = False
        if not passed:
            issue(code, address)

    def name(address, field, expected):
        check(address, 'RESOURCE_NAME', lambda v: v[field] == expected)

    prefix = f'queuerescue-{environment}'
    for n in FUNCTIONS:
        role = f'aws_iam_role.function["{n}"]'
        check(role, 'ROLE_BOUNDARY', lambda v: v['permissions_boundary'] == boundary)
        name(role, 'name', f'{prefix}-{n}')
        function = f'aws_lambda_function.function["{n}"]'
        name(function, 'function_name', f'{prefix}-{n}')
        if n in ['consumer', 'replay']:
            check(function, 'WORKER_RESERVATION', lambda v: type(v['reserved_concurrent_executions']) is int and v['reserved_concurrent_executions'] == 0)
        else:
            check(function, 'FUNCTION_RESERVATION', lambda v: type(v['reserved_concurrent_executions']) is int and v['reserved_concurrent_executions'] == -1)
        log = f'aws_cloudwatch_log_group.function["{n}"]'
        name(log, 'name', f'/aws/lambda/{prefix}-{n}')
        check(log, 'LOG_PROFILE', lambda v: v['retention_in_days'] == 1)
    check('aws_iam_role.workflow', 'ROLE_BOUNDARY', lambda v: v['permissions_boundary'] == boundary)
    name('aws_iam_role.workflow', 'name', prefix + '-workflow')
    for n in ['source', 'replay']:
        check(f'aws_lambda_event_source_mapping.shipment["{n}"]', 'MAPPING_IDLE', lambda v: v['enabled'] is False and v['scaling_config'] == [] and v['function_response_types'] == ['ReportBatchItemFailures'])
    for n in QUEUES:
        address = f'aws_sqs_queue.queue["{n}"]'
        name(address, 'name', prefix + '-' + n)
        check(address, 'QUEUE_PROFILE', lambda v: v['fifo_queue'] is False and v['sqs_managed_sse_enabled'] is True)
    for n in ['audit', 'business', 'ledger']:
        address = f'aws_dynamodb_table.data["{n}"]'
        name(address, 'name', prefix + '-' + n)
        check(address, 'TABLE_PROFILE', lambda v: v['billing_mode'] == 'PROVISIONED' and v['read_capacity'] == 5 and v['write_capacity'] == 5 and v['deletion_protection_enabled'] is True and v['stream_enabled'] is False and v['point_in_time_recovery'][0]['enabled'] is False)
        if n != 'ledger':
            check(address, 'TABLE_TTL', lambda v: v['ttl'] == [])
        else:
            check(address, 'TABLE_TTL', lambda v: v['ttl'][0]['enabled'] is True and v['ttl'][0]['attribute_name'] == 'expires_at')
    check('aws_s3_bucket.evidence', 'EVIDENCE_BUCKET', lambda v: v['bucket'] == f'{prefix}-{account_id}-{region}-evidence' and v['force_destroy'] is False)
    check('aws_s3_bucket_public_access_block.evidence', 'BUCKET_PUBLIC_ACCESS', lambda v: all(v[key] is True for key in ['block_public_acls', 'block_public_policy', 'ignore_public_acls', 'restrict_public_buckets']))
    check('aws_s3_bucket_versioning.evidence', 'BUCKET_VERSIONING', lambda v: v['versioning_configuration'][0]['status'] == 'Enabled')
    check('aws_s3_bucket_server_side_encryption_configuration.evidence', 'BUCKET_ENCRYPTION', lambda v: v['rule'][0]['apply_server_side_encryption_by_default'][0]['sse_algorithm'] == 'AES256')
    check('aws_cognito_user_pool.operators', 'COGNITO_PROFILE', lambda v: v['mfa_configuration'] == 'ON' and v['user_pool_tier'] == 'LITE' and v['deletion_protection'] == 'ACTIVE' and v['software_token_mfa_configuration'][0]['enabled'] is True and v['admin_create_user_config'][0]['allow_admin_create_user_only'] is True)
    name('aws_cognito_user_pool.operators', 'name', prefix + '-operators')
    name('aws_cognito_user_pool_domain.login', 'domain', prefix + '-' + account_id)
    callbacks = variables.get('callback_urls')
    origins = set()
    try:
        if not isinstance(callbacks, list) or not callbacks:
            raise ValueError()
        for url in callbacks:
            if not isinstance(url, str) or any(c.isspace() for c in url):
                raise ValueError()
            parsed = urlsplit(url)
            port = parsed.port
            if port is not None and not 1 <= port <= 65535:
                raise ValueError()
            if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname or not (parsed.scheme == 'https' or (parsed.scheme == 'http' and parsed.hostname == 'localhost' and parsed.port)):
                raise ValueError()
            origins.add(parsed.scheme + '://' + parsed.netloc)
    except (TypeError, ValueError):
        issue('CALLBACK_URLS')
        callbacks = []
    check('aws_cognito_user_pool_client.dashboard', 'COGNITO_CLIENT', lambda v: v['generate_secret'] is False and v['allowed_oauth_flows'] == ['code'] and set(v['allowed_oauth_scopes']) == {'openid', 'email', 'queuerescue/access'} and set(v['callback_urls']) == set(callbacks) and set(v['logout_urls']) == set(callbacks))
    check('aws_apigatewayv2_api.api', 'API_CORS', lambda v: set(v['cors_configuration'][0]['allow_origins']) == origins)
    name('aws_apigatewayv2_api.api', 'name', prefix)
    check('aws_apigatewayv2_authorizer.cognito', 'API_AUTH', lambda v: v['authorizer_type'] == 'JWT')
    check('aws_apigatewayv2_route.authenticated', 'API_AUTH', lambda v: v['authorization_type'] == 'JWT' and v['authorization_scopes'] == ['queuerescue/access'])
    check('aws_lambda_permission.api', 'API_PRINCIPAL', lambda v: v['principal'] == 'apigateway.amazonaws.com' and v['action'] == 'lambda:InvokeFunction')
    name('aws_cloudwatch_log_group.api', 'name', '/aws/apigateway/' + prefix)
    check('aws_cloudwatch_log_group.api', 'LOG_PROFILE', lambda v: v['retention_in_days'] == 1)
    name('aws_sfn_state_machine.recovery', 'name', prefix + '-recovery')
    check('aws_sfn_state_machine.recovery', 'WORKFLOW_PROFILE', lambda v: v['type'] == 'STANDARD')
    name('aws_sns_topic.alerts', 'name', prefix + '-alerts')
    if email:
        check('aws_sns_topic_subscription.email[0]', 'NOTIFICATION_PROFILE', lambda v: v['protocol'] == 'email' and v['endpoint'] == email)
        check('aws_budgets_budget.sandbox[0]', 'NOTIFICATION_PROFILE', lambda v: v['name'] == prefix + '-monthly' and v['budget_type'] == 'COST' and v['limit_unit'] == 'USD' and v['time_unit'] == 'MONTHLY')
    report['checks_passed'] = not report['issues']
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan-json', required=True)
    parser.add_argument('--account-id', required=True)
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--environment', choices=['dev', 'test'], default='dev')
    args = parser.parse_args()
    try:
        raw = Path(args.plan_json).read_bytes()
        report = review_plan(json.loads(raw), args.account_id, args.region, args.environment)
    except (OSError, ValueError, TypeError):
        parser.error('Cannot review this target or JSON file. No report was written.')
    report['plan_sha256'] = sha256(raw).hexdigest()
    save_private_json('.data/aws-plan-review.json', report)
    print('Selected offline plan checks:', 'PASS' if report['checks_passed'] else 'FAIL')
    for code in sorted({issue['code'] for issue in report['issues']}):
        print(code)
    print('Saved private report. Deployment approval, live checks and cost review remain required.')
    if not report['checks_passed']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
