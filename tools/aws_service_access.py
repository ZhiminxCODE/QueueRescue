"""Prepare scoped minimal-stack service policies offline, without granting access.

Unknown AWS-assigned IDs are blockers, never wildcard write grants. The first
bootstrap, all effective access, costs and teardown need administrator review.
"""
import argparse
import json
from pathlib import Path
import re
from uuid import UUID

from tools.aws_readiness import save_private_json
from tools.aws_role_access import role_access_proposal


def service_access_proposal(account_id, region, environment, bindings=None):
    role_access_proposal(account_id, region, environment)  # Shared target validation.
    allowed = {'account_id', 'region', 'environment', 'api_id', 'cognito_user_pool_id',
               'event_source_mapping_ids', 'notification_email', 'cost_profile',
               'audit_logging_enabled', 'dashboard_hosting_enabled'}
    if bindings is not None:
        if not isinstance(bindings, dict) or set(bindings) - allowed:
            raise ValueError('Use only the documented reviewed-binding fields')
        if any(bindings.get(k) != v for k, v in [('account_id', account_id), ('region', region), ('environment', environment)]):
            raise ValueError('Reviewed bindings must match the exact target')
    bindings = bindings or {}
    if bindings.get('cost_profile', 'minimal') != 'minimal' or any(bindings.get(k, False) is not False for k in ['audit_logging_enabled', 'dashboard_hosting_enabled']):
        raise ValueError('This proposal covers only the minimal base stack, without optional audit or hosting')
    api_id = bindings.get('api_id')
    pool_id = bindings.get('cognito_user_pool_id')
    mappings = bindings.get('event_source_mapping_ids')
    email = bindings.get('notification_email')
    if api_id is not None and (not isinstance(api_id, str) or not re.fullmatch(r'[a-z0-9]{1,20}', api_id)):
        raise ValueError('Use an exact API Gateway API ID, not an ARN, URL or wildcard')
    if pool_id is not None and (not isinstance(pool_id, str) or not re.fullmatch(re.escape(region) + r'_[A-Za-z0-9]{1,55}', pool_id)):
        raise ValueError('Use an exact user pool ID in the reviewed region')
    if mappings is not None:
        if not isinstance(mappings, dict) or set(mappings) != {'source', 'replay'}:
            raise ValueError('Bind both source and replay event mappings')
        try:
            if any(not isinstance(v, str) or str(UUID(v)) != v for v in mappings.values()) or len(set(mappings.values())) != 2:
                raise ValueError()
        except (ValueError, AttributeError):
            raise ValueError('Use two distinct canonical event-mapping UUIDs') from None
    if email is not None and (not isinstance(email, str) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email)):
        raise ValueError('Use a reviewed notification email without whitespace')

    prefix = f'queuerescue-{environment}'
    regional = f'arn:aws:{{service}}:{region}:{account_id}:'
    functions = {n: regional.format(service='lambda') + f'function:{prefix}-{n}' for n in ['api', 'collector', 'replay', 'consumer', 'verifier']}
    tables = [regional.format(service='dynamodb') + f'table/{prefix}-{n}' for n in ['ledger', 'business', 'audit']]
    queues = [regional.format(service='sqs') + f'{prefix}-{n}' for n in ['source', 'source-dlq', 'replay', 'replay-dlq']]
    bucket = f'arn:aws:s3:::{prefix}-{account_id}-{region}-evidence'
    workflow = regional.format(service='states') + f'stateMachine:{prefix}-recovery'
    topic = regional.format(service='sns') + f'{prefix}-alerts'
    policies = {}

    def add(service, sid, actions, resources, condition=None, effect='Allow', regional_request=True):
        conditions = {'aws:RequestedRegion': region} if regional_request else {}
        conditions.update(condition or {})
        s = {'Sid': sid, 'Effect': effect, 'Action': [service + ':' + a for a in actions], 'Resource': resources}
        if conditions:
            s['Condition'] = {'StringEquals': conditions}
        policies.setdefault(service, {'Version': '2012-10-17', 'Statement': []})['Statement'].append(s)

    add('s3', 'CreateDedicatedBucket', ['CreateBucket'], [bucket])
    add('s3', 'ConfigureOwnedBucket', ['ListBucket', 'GetBucketLocation', 'GetBucketAcl', 'GetBucketCORS', 'GetBucketLogging', 'GetBucketNotification', 'GetBucketRequestPayment', 'GetBucketWebsite', 'GetAccelerateConfiguration', 'GetReplicationConfiguration', 'GetBucketTagging', 'GetBucketVersioning', 'GetBucketPolicy', 'GetBucketPolicyStatus', 'GetBucketPublicAccessBlock', 'GetBucketOwnershipControls', 'GetBucketObjectLockConfiguration', 'GetEncryptionConfiguration', 'GetLifecycleConfiguration', 'PutBucketTagging', 'PutBucketVersioning', 'PutBucketPolicy', 'PutBucketPublicAccessBlock', 'PutBucketOwnershipControls', 'PutEncryptionConfiguration', 'PutLifecycleConfiguration'], [bucket], {'s3:ResourceAccount': account_id})
    add('s3', 'KeepEvidenceObjects', ['GetObject', 'GetObjectVersion', 'PutObject', 'DeleteObject', 'DeleteObjectVersion'], [bucket + '/*'], effect='Deny', regional_request=False)
    add('s3', 'KeepBucket', ['DeleteBucket'], [bucket], effect='Deny', regional_request=False)
    add('dynamodb', 'ConfigureNamedTables', ['CreateTable', 'DescribeTable', 'UpdateTable', 'DescribeContinuousBackups', 'UpdateContinuousBackups', 'DescribeTimeToLive', 'UpdateTimeToLive', 'ListTagsOfResource', 'TagResource', 'UntagResource'], tables)
    add('dynamodb', 'KeepTableData', ['DeleteTable', 'GetItem', 'PutItem', 'UpdateItem', 'DeleteItem', 'Query', 'Scan', 'ConditionCheckItem'], tables, effect='Deny', regional_request=False)
    add('sqs', 'ConfigureNamedQueues', ['CreateQueue', 'GetQueueAttributes', 'GetQueueUrl', 'SetQueueAttributes', 'ListQueueTags', 'TagQueue', 'UntagQueue'], queues)
    add('sqs', 'KeepQueueData', ['DeleteQueue', 'PurgeQueue', 'SendMessage', 'ReceiveMessage', 'DeleteMessage', 'ChangeMessageVisibility'], queues, effect='Deny', regional_request=False)
    add('lambda', 'ConfigureNamedFunctions', ['CreateFunction', 'GetFunction', 'GetFunctionConfiguration', 'GetFunctionConcurrency', 'GetRuntimeManagementConfig', 'GetPolicy', 'ListVersionsByFunction', 'UpdateFunctionCode', 'UpdateFunctionConfiguration', 'ListTags', 'TagResource', 'UntagResource'], list(functions.values()))
    add('lambda', 'ConfigureWorkerReservations', ['PutFunctionConcurrency'], [functions['consumer'], functions['replay']])
    add('lambda', 'AllowHttpGatewayOnly', ['AddPermission'], [functions['api']], {'lambda:Principal': 'apigateway.amazonaws.com'})
    add('lambda', 'MaintainHttpPermission', ['RemovePermission'], [functions['api']])
    add('lambda', 'KeepFunctionsAndRuntimeSeparation', ['DeleteFunction', 'InvokeFunction', 'DeleteFunctionConcurrency'], list(functions.values()), effect='Deny', regional_request=False)
    if mappings:
        arns = [regional.format(service='lambda') + 'event-source-mapping:' + v for v in mappings.values()]
        add('lambda', 'ManageBoundConsumerMappings', ['GetEventSourceMapping', 'UpdateEventSourceMapping'], arns, {'lambda:FunctionArn': functions['consumer']})
        add('lambda', 'TagBoundMappings', ['ListTags', 'TagResource', 'UntagResource'], arns)
    add('states', 'ConfigureRecoveryWorkflow', ['CreateStateMachine', 'DescribeStateMachine', 'UpdateStateMachine', 'ListTagsForResource', 'TagResource', 'UntagResource'], [workflow])
    add('states', 'KeepWorkflowAndRuntimeSeparation', ['DeleteStateMachine', 'StartExecution', 'StartSyncExecution'], [workflow], effect='Deny', regional_request=False)
    groups = [regional.format(service='logs') + f'log-group:/aws/lambda/{prefix}-{n}' for n in functions]
    groups.append(regional.format(service='logs') + f'log-group:/aws/apigateway/{prefix}')
    group_resources = [r for group in groups for r in [group, group + ':*']]
    add('logs', 'ConfigureNamedLogGroups', ['CreateLogGroup', 'PutRetentionPolicy', 'ListTagsForResource', 'ListTagsLogGroup', 'TagResource', 'UntagResource'], group_resources)
    add('logs', 'DescribeRegionalLogGroupMetadata', ['DescribeLogGroups'], ['*'])
    add('logs', 'KeepLogEvidence', ['DeleteLogGroup', 'DeleteLogStream', 'DeleteRetentionPolicy'], group_resources, effect='Deny', regional_request=False)
    add('sns', 'ConfigureAlertTopic', ['CreateTopic', 'GetTopicAttributes', 'SetTopicAttributes', 'ListTagsForResource', 'TagResource', 'UntagResource'], [topic])
    if email:
        add('sns', 'SubscribeReviewedEmailOnly', ['Subscribe'], [topic], {'sns:Protocol': 'email', 'sns:Endpoint': email})
        add('sns', 'InspectTopicSubscriptions', ['GetSubscriptionAttributes', 'SetSubscriptionAttributes', 'ListSubscriptionsByTopic'], [topic])
        add('budgets', 'ManageExactNotificationBudget', ['ViewBudget', 'ModifyBudget', 'ListTagsForResource', 'TagResource', 'UntagResource'], [f'arn:aws:budgets::{account_id}:budget/{prefix}-monthly'], regional_request=False)
    if pool_id:
        pool = regional.format(service='cognito-idp') + 'userpool/' + pool_id
        add('cognito-idp', 'ConfigureBoundUserPool', ['DescribeUserPool', 'UpdateUserPool', 'GetUserPoolMfaConfig', 'SetUserPoolMfaConfig', 'ListTagsForResource', 'TagResource', 'UntagResource', 'CreateGroup', 'GetGroup', 'ListGroups', 'UpdateGroup', 'CreateResourceServer', 'DescribeResourceServer', 'ListResourceServers', 'UpdateResourceServer', 'CreateUserPoolClient', 'DescribeUserPoolClient', 'ListUserPoolClients', 'UpdateUserPoolClient', 'CreateUserPoolDomain', 'UpdateUserPoolDomain'], [pool])
        add('cognito-idp', 'KeepUserPool', ['DeleteUserPool'], [pool], effect='Deny', regional_request=False)
    add('cognito-idp', 'DescribeRegionalDomainMetadata', ['DescribeUserPoolDomain'], ['*'])
    if api_id:
        from urllib.parse import quote
        api = f'arn:aws:apigateway:{region}::/apis/{api_id}'
        add('apigateway', 'ConfigureBoundHttpApi', ['GET', 'PATCH', 'PUT'], [api, api + '/*'])
        add('apigateway', 'ManageBoundApiChildren', ['POST', 'DELETE'], [api + '/*'])
        add('apigateway', 'ManageExactApiTags', ['GET', 'POST', 'DELETE'], [f'arn:aws:apigateway:{region}::/tags/' + quote(r, safe='') for r in [api, api + '/stages/$default']])
        add('apigateway', 'KeepHttpApi', ['DELETE'], [api], effect='Deny', regional_request=False)

    missing = [key for key, value in [('api_id', api_id), ('cognito_user_pool_id', pool_id), ('event_source_mapping_ids', mappings)] if not value]
    # Administrators can review service fragments individually, then attach the
    # same statements in documents within IAM's managed-policy size limit.
    managed = []
    current = {'Version': '2012-10-17', 'Statement': []}
    for policy in policies.values():
        for s in policy['Statement']:
            candidate = {'Version': '2012-10-17', 'Statement': current['Statement'] + [s]}
            if len(json.dumps(candidate, separators=(',', ':'))) > 6144:
                if not current['Statement']:
                    raise ValueError('Target values exceed the managed-policy document limit')
                managed.append(current)
                candidate = {'Version': '2012-10-17', 'Statement': [s]}
                if len(json.dumps(candidate, separators=(',', ':'))) > 6144:
                    raise ValueError('Target values exceed the managed-policy document limit')
            current = candidate
    if current['Statement']:
        managed.append(current)
    return {'scope': 'Minimal base service provisioning and maintenance proposal; no grant or teardown approval',
            'deployment_ready': False, 'policy_bindings_complete': not missing, 'missing_bindings': missing,
            'service_policies': policies,
            'managed_policies': managed,
            'public_summary': {'service_policy_count': len(policies), 'missing_binding_count': len(missing), 'notification_permissions_included': bool(email)},
            'administrator_requirements': [
                'First deployment: an authorized administrator must review and bootstrap the API, user pool and two event mappings, then capture and verify exact account/region/resource ownership in the binding file. This tool does not generate broad collection-create or mapping-create access. Preserve/import the same Terraform resource addresses; do not recreate them after binding.',
                'API Gateway log delivery can require logs:CreateLogDelivery, logs:UpdateLogDelivery, logs:DeleteLogDelivery, logs:GetLogDelivery, logs:ListLogDeliveries, logs:PutResourcePolicy and logs:DescribeResourcePolicies on Resource *. These regional account-wide operations need a separately reviewed administrator-controlled bootstrap; they are deliberately not in the delegated service policies. Verify the actual delivery path with the saved plan before applying.',
                'The regional metadata exceptions are logs:DescribeLogGroups and cognito-idp:DescribeUserPoolDomain. Neither supports a resource ARN; no payload or Cost Explorer reads are granted.',
                'Review the existing IAM delegation fragment, administrator-owned execution boundary, all effective caller policies, resource policies and role ownership separately. Service fragments do not grant iam:PassRole or IAM administration.',
                'Configuration powers are privileged: Lambda code/role updates, pool/client settings, API routes, bucket/queue/topic policies, lifecycle/TTL, backups and retention can affect data access, availability and costs. Direct-data denials do not protect against malicious application-code changes by a trusted deployer.',
                'Notification permissions must match the ignored Terraform email. budgets:ModifyBudget is coarse and can also delete the exact budget; it is not a spending cap. Budgets uses a global account/name ARN without a regional restriction.',
                'No standard KMS, optional audit/archive, CloudFront hosting, application-user management, seeding, runtime invocation or destructive teardown grant is included. Some replacement updates need separately reviewed access.',
                'AWS policy/service validation, quota and cost decisions, notification delivery, concrete saved-plan approval and cleanup approval remain required. Input syntax and offline tests do not establish resource ownership or live authorization.',
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account-id', required=True)
    parser.add_argument('--region', default='us-east-1')
    parser.add_argument('--environment', choices=['dev', 'test'], default='dev')
    parser.add_argument('--bindings', help='Ignored, independently reviewed resource-binding JSON')
    args = parser.parse_args()
    try:
        bindings = json.loads(Path(args.bindings).read_text()) if args.bindings else None
        proposal = service_access_proposal(args.account_id, args.region, args.environment, bindings)
    except (OSError, ValueError, TypeError):
        parser.error('Invalid target or binding file; use the documented reviewed schema. No proposal was written.')
    save_private_json('.data/aws-service-access.json', proposal)
    print('Saved private service proposal. No AWS calls or permission changes.')
    print('Unresolved resource bindings:', len(proposal['missing_bindings']))
    print('Administrator bootstrap, effective-policy review and deployment approval remain required.')


if __name__ == '__main__':
    main()
