import io
import json
from fnmatch import fnmatchcase
from pathlib import Path
import unittest
from unittest.mock import patch

from tools.aws_service_access import main, service_access_proposal

ACCOUNT = '123456789012'
REGION = 'us-east-1'
BINDINGS = {'account_id': ACCOUNT, 'region': REGION, 'environment': 'dev', 'api_id': 'example123',
            'cognito_user_pool_id': 'us-east-1_Example123',
            'event_source_mapping_ids': {'source': '11111111-1111-4111-8111-111111111111', 'replay': '22222222-2222-4222-8222-222222222222'}}


def permits(bundle, action, resource, extra=None):
    context = {'aws:RequestedRegion': REGION, **(extra or {})}
    granted = False
    for policy in bundle['service_policies'].values():
        for s in policy['Statement']:
            if not any(fnmatchcase(action.lower(), a.lower()) for a in s['Action']): continue
            if not any(fnmatchcase(resource, r) for r in s['Resource']): continue
            if not all(context.get(k) == v for k, v in s.get('Condition', {}).get('StringEquals', {}).items()): continue
            if s['Effect'] == 'Deny': return False
            granted = True
    return granted


class ServiceAccessTests(unittest.TestCase):
    def setUp(self):
        self.bundle = service_access_proposal(ACCOUNT, REGION, 'dev', BINDINGS)

    def test_missing_bindings_do_not_become_wildcard_write_access(self):
        bundle = service_access_proposal(ACCOUNT, REGION, 'dev')
        self.assertFalse(bundle['policy_bindings_complete'])
        self.assertFalse(bundle['deployment_ready'])
        self.assertEqual(set(bundle['missing_bindings']), {'api_id', 'cognito_user_pool_id', 'event_source_mapping_ids'})
        for policy in bundle['service_policies'].values():
            for s in policy['Statement']:
                self.assertTrue(all('*' not in a for a in s['Action']))
                if s['Resource'] == ['*']:
                    self.assertTrue(set(s['Action']) <= {'logs:DescribeLogGroups', 'cognito-idp:DescribeUserPoolDomain'})
        self.assertFalse(permits(bundle, 'cognito-idp:CreateUserPool', '*'))
        self.assertFalse(permits(bundle, 'apigateway:POST', f'arn:aws:apigateway:{REGION}::/apis'))
        self.assertFalse(permits(bundle, 'lambda:CreateEventSourceMapping', '*'))

    def test_complete_bindings_still_require_review_and_administrator_bootstrap(self):
        self.assertTrue(self.bundle['policy_bindings_complete'])
        self.assertFalse(self.bundle['deployment_ready'])
        self.assertTrue(self.bundle['administrator_requirements'])
        self.assertIn('logs:PutResourcePolicy', json.dumps(self.bundle['administrator_requirements']))
        self.assertNotIn('logs:PutResourcePolicy', json.dumps(self.bundle['service_policies']))

    def test_named_storage_resources_are_exact_and_regional(self):
        table = f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/queuerescue-dev-ledger'
        queue = f'arn:aws:sqs:{REGION}:{ACCOUNT}:queuerescue-dev-source'
        self.assertTrue(permits(self.bundle, 'dynamodb:CreateTable', table))
        self.assertTrue(permits(self.bundle, 'sqs:SetQueueAttributes', queue))
        for value in [table+'-unrelated', table.replace(ACCOUNT, '999999999999'), table.replace('-dev-', '-test-')]:
            self.assertFalse(permits(self.bundle, 'dynamodb:UpdateTable', value))
        self.assertFalse(permits(self.bundle, 'dynamodb:UpdateTable', table, {'aws:RequestedRegion': 'us-west-2'}))

    def test_bucket_configuration_checks_owner_and_excludes_payload_access(self):
        bucket = f'arn:aws:s3:::queuerescue-dev-{ACCOUNT}-{REGION}-evidence'
        self.assertTrue(permits(self.bundle, 's3:CreateBucket', bucket))
        self.assertTrue(permits(self.bundle, 's3:PutBucketPolicy', bucket, {'s3:ResourceAccount': ACCOUNT}))
        self.assertFalse(permits(self.bundle, 's3:PutBucketPolicy', bucket, {'s3:ResourceAccount': '999999999999'}))
        for action in ['s3:GetObject', 's3:PutObject', 's3:DeleteObject', 's3:DeleteObjectVersion']:
            self.assertFalse(permits(self.bundle, action, bucket+'/evidence/private.json'))
        self.assertFalse(permits(self.bundle, 's3:DeleteBucket', bucket))

    def test_only_bound_api_and_its_children_are_managed(self):
        api = f'arn:aws:apigateway:{REGION}::/apis/example123'
        for path, action in [('', 'apigateway:PATCH'), ('/authorizers', 'apigateway:POST'), ('/routes/id', 'apigateway:DELETE')]:
            self.assertTrue(permits(self.bundle, action, api+path))
        self.assertFalse(permits(self.bundle, 'apigateway:DELETE', api))
        self.assertFalse(permits(self.bundle, 'apigateway:POST', api.replace('example123', 'other')+'/routes'))
        self.assertFalse(permits(self.bundle, 'apigateway:POST', api.replace('example123', 'example123extra')+'/routes'))

    def test_cognito_scope_excludes_users_and_pool_destruction(self):
        pool = f'arn:aws:cognito-idp:{REGION}:{ACCOUNT}:userpool/{BINDINGS["cognito_user_pool_id"]}'
        self.assertTrue(permits(self.bundle, 'cognito-idp:CreateGroup', pool))
        self.assertTrue(permits(self.bundle, 'cognito-idp:UpdateUserPoolClient', pool))
        for action in ['cognito-idp:DeleteUserPool', 'cognito-idp:AdminCreateUser', 'cognito-idp:AdminAddUserToGroup', 'cognito-idp:AdminSetUserPassword']:
            self.assertFalse(permits(self.bundle, action, pool))
        self.assertFalse(permits(self.bundle, 'cognito-idp:UpdateUserPool', pool+'Extra'))

    def test_mapping_management_uses_exact_ids_and_consumer_function(self):
        consumer = f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:queuerescue-dev-consumer'
        for uuid in BINDINGS['event_source_mapping_ids'].values():
            arn = f'arn:aws:lambda:{REGION}:{ACCOUNT}:event-source-mapping:{uuid}'
            self.assertTrue(permits(self.bundle, 'lambda:UpdateEventSourceMapping', arn, {'lambda:FunctionArn': consumer}))
            self.assertFalse(permits(self.bundle, 'lambda:UpdateEventSourceMapping', arn, {'lambda:FunctionArn': consumer+'extra'}))
            self.assertFalse(permits(self.bundle, 'lambda:DeleteEventSourceMapping', arn))
            self.assertFalse(permits(self.bundle, 'lambda:TagResource', arn+'extra'))

    def test_lambda_permission_cannot_make_an_arbitrary_principal_invoke(self):
        api = f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:queuerescue-dev-api'
        self.assertTrue(permits(self.bundle, 'lambda:AddPermission', api, {'lambda:Principal': 'apigateway.amazonaws.com'}))
        self.assertFalse(permits(self.bundle, 'lambda:AddPermission', api, {'lambda:Principal': '*'}))
        self.assertFalse(permits(self.bundle, 'lambda:AddPermission', api.replace('-api', '-consumer'), {'lambda:Principal': 'apigateway.amazonaws.com'}))
        self.assertFalse(permits(self.bundle, 'lambda:DeleteFunctionConcurrency', api))

    def test_direct_data_and_runtime_operations_are_not_deployment_permissions(self):
        resources = [
            ('dynamodb:PutItem', f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/queuerescue-dev-business'),
            ('sqs:SendMessage', f'arn:aws:sqs:{REGION}:{ACCOUNT}:queuerescue-dev-source'),
            ('sqs:PurgeQueue', f'arn:aws:sqs:{REGION}:{ACCOUNT}:queuerescue-dev-source'),
            ('lambda:InvokeFunction', f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:queuerescue-dev-api'),
            ('states:StartExecution', f'arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:queuerescue-dev-recovery')]
        for action, arn in resources:self.assertFalse(permits(self.bundle, action, arn))
        self.assertNotIn('iam:', json.dumps(self.bundle['service_policies']))
        self.assertNotIn('kms:', json.dumps(self.bundle['service_policies']))
        self.assertNotIn('cloudfront:', json.dumps(self.bundle['service_policies']))
        self.assertNotIn('cloudtrail:', json.dumps(self.bundle['service_policies']))

    def test_notifications_require_explicit_email_and_exact_topic(self):
        topic = f'arn:aws:sns:{REGION}:{ACCOUNT}:queuerescue-dev-alerts'
        self.assertFalse(permits(self.bundle, 'sns:Subscribe', topic))
        bindings = {**BINDINGS, 'notification_email': 'alerts@example.test'}
        bundle = service_access_proposal(ACCOUNT, REGION, 'dev', bindings)
        extra = {'sns:Protocol': 'email', 'sns:Endpoint': 'alerts@example.test'}
        self.assertTrue(permits(bundle, 'sns:Subscribe', topic, extra))
        self.assertFalse(permits(bundle, 'sns:Subscribe', topic, {**extra, 'sns:Endpoint': 'other@example.test'}))
        self.assertFalse(permits(bundle, 'sns:Subscribe', topic, {**extra, 'sns:Protocol': 'https'}))
        self.assertTrue(permits(bundle, 'budgets:ModifyBudget', f'arn:aws:budgets::{ACCOUNT}:budget/queuerescue-dev-monthly'))
        self.assertFalse(permits(bundle, 'budgets:ModifyBudget', f'arn:aws:budgets::{ACCOUNT}:budget/other'))
        self.assertNotIn('alerts@example.test', json.dumps(bundle['public_summary']))

    def test_binding_target_or_profile_mismatch_is_rejected(self):
        for changes in [{'account_id':'999999999999'}, {'region':'us-west-2'}, {'environment':'test'}, {'cost_profile':'standard'}, {'audit_logging_enabled':True}, {'dashboard_hosting_enabled':True}, {'surprise':'value'}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                service_access_proposal(ACCOUNT, REGION, 'dev', {**BINDINGS, **changes})

    def test_invalid_ids_and_cross_region_pools_are_rejected(self):
        for key,value in [('api_id','*'), ('api_id','valid/routes'), ('cognito_user_pool_id','us-west-2_Pool'), ('notification_email','bad\naddress@example.test')]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                service_access_proposal(ACCOUNT,REGION,'dev',{**BINDINGS,key:value})
        for mappings in [{'source':'*','replay':BINDINGS['event_source_mapping_ids']['replay']}, {'source':BINDINGS['event_source_mapping_ids']['source']}, dict.fromkeys(['source','replay'],BINDINGS['event_source_mapping_ids']['source'])]:
            with self.assertRaises(ValueError):service_access_proposal(ACCOUNT,REGION,'dev',{**BINDINGS,'event_source_mapping_ids':mappings})

    def test_service_policies_fit_managed_policy_size_limit(self):
        for p in self.bundle['service_policies'].values():
            self.assertLessEqual(len(json.dumps(p,separators=(',',':'))),6144)
        for p in self.bundle['managed_policies']:
            self.assertLessEqual(len(json.dumps(p,separators=(',',':'))),6144)
        original=[s for p in self.bundle['service_policies'].values() for s in p['Statement']]
        packed=[s for p in self.bundle['managed_policies'] for s in p['Statement']]
        self.assertEqual(packed,original)

    def test_official_action_catalog_confirms_actions_and_unscoped_exceptions(self):
        catalog=json.loads(Path('tests/fixtures/aws_service_authorization.json').read_text())
        for policy in self.bundle['service_policies'].values():
            for s in policy['Statement']:
                for action in s['Action']:
                    entry=catalog['actions'][action]
                    if s['Resource']==['*']:self.assertFalse(entry['resource_types'],action)
                    else:self.assertTrue(entry['resource_types'],action)
                    for key in s.get('Condition',{}).get('StringEquals',{}):
                        if key!='aws:RequestedRegion':self.assertIn(key,entry['condition_keys'],action)
        self.assertTrue(catalog['sources'])

    def test_cli_writes_private_bundle_without_echoing_targets(self):
        with patch('sys.argv',['aws_service_access','--account-id',ACCOUNT]),patch('tools.aws_service_access.save_private_json') as save,patch('sys.stdout',new_callable=io.StringIO) as output:
            main()
        self.assertEqual(save.call_args.args[0],'.data/aws-service-access.json')
        self.assertNotIn(ACCOUNT,output.getvalue())
        self.assertFalse(save.call_args.args[1]['deployment_ready'])

    def test_cli_reads_bindings_and_stops_on_invalid_input(self):
        with patch('sys.argv',['aws_service_access','--account-id',ACCOUNT,'--bindings','reviewed.json']),patch('tools.aws_service_access.Path.read_text',return_value=json.dumps(BINDINGS)),patch('tools.aws_service_access.save_private_json') as save,patch('sys.stdout',new_callable=io.StringIO):main()
        self.assertTrue(save.call_args.args[1]['policy_bindings_complete'])
        with patch('sys.argv',['aws_service_access','--account-id','invalid']),patch('tools.aws_service_access.save_private_json') as save,patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):main()
        save.assert_not_called()

    def test_partial_bindings_do_not_unlock_other_services(self):
        partial={k:v for k,v in BINDINGS.items() if k in ['account_id','region','environment','api_id']}
        bundle=service_access_proposal(ACCOUNT,REGION,'dev',partial)
        self.assertEqual(set(bundle['missing_bindings']),{'cognito_user_pool_id','event_source_mapping_ids'})
        self.assertNotIn('UpdateUserPool',json.dumps(bundle['service_policies']))
        self.assertNotIn('UpdateEventSourceMapping',json.dumps(bundle['service_policies']))

    def test_test_environment_and_notification_actions_are_in_catalog(self):
        bindings={**BINDINGS,'environment':'test','notification_email':'alerts@example.test'}
        bundle=service_access_proposal(ACCOUNT,REGION,'test',bindings)
        self.assertNotIn('queuerescue-dev',json.dumps(bundle['service_policies']))
        catalog=json.loads(Path('tests/fixtures/aws_service_authorization.json').read_text())
        for policy in bundle['service_policies'].values():
            for s in policy['Statement']:
                for action in s['Action']:self.assertIn(action,catalog['actions'])
