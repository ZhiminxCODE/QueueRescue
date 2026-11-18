import copy
import io
import json
from hashlib import sha256
import unittest
from unittest.mock import patch

from tools.review_aws_plan import main, review_plan

ACCOUNT='123456789012'
BOUNDARY=f'arn:aws:iam::{ACCOUNT}:policy/queuerescue-dev-execution-boundary'


def base_plan():
    """Authored synthetic plan contract; no saved account plan or runtime data."""
    variables={'aws_account_id':ACCOUNT,'aws_region':'us-east-1','environment':'dev','cost_profile':'minimal',
               'consumers_enabled':False,'replay_enabled':False,'audit_logging_enabled':False,'audit_collection_enabled':False,
               'dashboard_hosting_enabled':False,'dashboard_delivery_enabled':False,'protect_data':True,
               'allow_destroy_evidence':False,'execution_permissions_boundary_arn':BOUNDARY,'budget_alert_email':'',
               'callback_urls':['http://localhost:8000/']}
    resources=[]
    def add(address,**values):
        resources.append({'address':address,'mode':'managed','type':address.split('.')[0],
                          'provider_name':'registry.terraform.io/hashicorp/aws',
                          'change':{'actions':['create'],'after':values,'after_unknown':{}}})
    for n in ['api','collector','consumer','replay','verifier']:
        add(f'aws_iam_role.function["{n}"]',name=f'queuerescue-dev-{n}',permissions_boundary=BOUNDARY)
        add(f'aws_iam_role_policy.function["{n}"]')
        add(f'aws_lambda_function.function["{n}"]',function_name=f'queuerescue-dev-{n}',reserved_concurrent_executions=0 if n in ['consumer','replay'] else -1)
        add(f'aws_cloudwatch_log_group.function["{n}"]',name=f'/aws/lambda/queuerescue-dev-{n}',retention_in_days=1)
    add('aws_iam_role.workflow',name='queuerescue-dev-workflow',permissions_boundary=BOUNDARY)
    add('aws_iam_role_policy.workflow')
    for n in ['audit','business','ledger']:
        add(f'aws_dynamodb_table.data["{n}"]',name=f'queuerescue-dev-{n}',billing_mode='PROVISIONED',read_capacity=5,write_capacity=5,
            deletion_protection_enabled=True,stream_enabled=False,point_in_time_recovery=[{'enabled':False}],
            ttl=[{'enabled':True,'attribute_name':'expires_at'}] if n=='ledger' else [])
    for n in ['source','replay','source-dlq','replay-dlq']:
        add(f'aws_sqs_queue.queue["{n}"]',name=f'queuerescue-dev-{n}',fifo_queue=False,sqs_managed_sse_enabled=True)
        add(f'aws_sqs_queue_policy.tls["{n}"]')
    for n in ['source','replay']:
        add(f'aws_sqs_queue_redrive_policy.source["{n}"]')
        add(f'aws_sqs_queue_redrive_allow_policy.dlq["{n}"]')
        add(f'aws_lambda_event_source_mapping.shipment["{n}"]',enabled=False,scaling_config=[],function_response_types=['ReportBatchItemFailures'])
    add('aws_s3_bucket.evidence',bucket=f'queuerescue-dev-{ACCOUNT}-us-east-1-evidence',force_destroy=False)
    add('aws_s3_bucket_public_access_block.evidence',**dict.fromkeys(['block_public_acls','block_public_policy','ignore_public_acls','restrict_public_buckets'],True))
    add('aws_s3_bucket_versioning.evidence',versioning_configuration=[{'status':'Enabled'}])
    add('aws_s3_bucket_server_side_encryption_configuration.evidence',rule=[{'apply_server_side_encryption_by_default':[{'sse_algorithm':'AES256'}]}])
    add('aws_s3_bucket_lifecycle_configuration.evidence')
    add('aws_s3_bucket_policy.evidence')
    add('aws_cognito_user_pool.operators',name='queuerescue-dev-operators',user_pool_tier='LITE',deletion_protection='ACTIVE',mfa_configuration='ON',software_token_mfa_configuration=[{'enabled':True}],admin_create_user_config=[{'allow_admin_create_user_only':True}])
    for n in ['Admin','Approver','Operator','Viewer']:add(f'aws_cognito_user_group.role["{n}"]')
    add('aws_cognito_resource_server.api')
    add('aws_cognito_user_pool_client.dashboard',generate_secret=False,allowed_oauth_flows=['code'],allowed_oauth_scopes=['openid','email','queuerescue/access'],callback_urls=variables['callback_urls'],logout_urls=variables['callback_urls'])
    add('aws_cognito_user_pool_domain.login',domain=f'queuerescue-dev-{ACCOUNT}')
    add('aws_apigatewayv2_api.api',name='queuerescue-dev',cors_configuration=[{'allow_origins':['http://localhost:8000']}])
    add('aws_apigatewayv2_authorizer.cognito',authorizer_type='JWT')
    add('aws_apigatewayv2_integration.lambda')
    add('aws_apigatewayv2_route.authenticated',authorization_type='JWT',authorization_scopes=['queuerescue/access'])
    add('aws_apigatewayv2_stage.default')
    add('aws_lambda_permission.api',principal='apigateway.amazonaws.com',action='lambda:InvokeFunction')
    add('aws_cloudwatch_log_group.api',name='/aws/apigateway/queuerescue-dev',retention_in_days=1)
    add('aws_sfn_state_machine.recovery',name='queuerescue-dev-recovery',type='STANDARD')
    add('aws_sns_topic.alerts',name='queuerescue-dev-alerts')
    return {'format_version':'1.2','terraform_version':'1.17.2','variables':{k:{'value':v} for k,v in variables.items()},'resource_changes':resources}


def resource(plan,address):return next(r for r in plan['resource_changes'] if r['address']==address)


class PlanReviewTests(unittest.TestCase):
    def check(self,plan):return review_plan(plan,ACCOUNT,'us-east-1','dev')
    def assert_rejected(self,plan,code):
        report=self.check(plan);self.assertFalse(report['checks_passed']);self.assertIn(code,{i['code'] for i in report['issues']})

    def test_idle_plan_passes_selected_checks_without_authorizing_deployment(self):
        report=self.check(base_plan());self.assertTrue(report['checks_passed'],report['issues']);self.assertFalse(report['deployment_ready'])
        self.assertEqual(report['resource_count'],62);self.assertTrue(report['manual_reviews'])

    def test_unsupported_or_partial_formats_are_rejected(self):
        for plan in [{},[],{'format_version':'2.0'}, {'plan_format_version':'1.2'}]:self.assert_rejected(plan,'PLAN_FORMAT')
        plan=base_plan();plan['complete']=False;self.assert_rejected(plan,'PLAN_INCOMPLETE')
        plan=base_plan();plan['deferred_changes']=[{}];self.assert_rejected(plan,'PLAN_INCOMPLETE')

    def test_target_profile_and_strict_boolean_values(self):
        for key,value in [('aws_account_id','999999999999'),('aws_region','us-west-2'),('environment','production'),('cost_profile','standard'),('consumers_enabled',True),('replay_enabled',True),('audit_logging_enabled',True),('dashboard_hosting_enabled',True),('protect_data',False),('allow_destroy_evidence',True),('consumers_enabled',0)]:
            plan=base_plan();plan['variables'][key]['value']=value
            self.assert_rejected(plan,'VARIABLE_'+key.upper())
        plan=base_plan();del plan['variables']['replay_enabled'];self.assert_rejected(plan,'VARIABLE_REPLAY_ENABLED')

    def test_every_runtime_role_requires_exact_external_boundary(self):
        plan=base_plan();plan['variables']['execution_permissions_boundary_arn']['value']=None;self.assert_rejected(plan,'VARIABLE_EXECUTION_PERMISSIONS_BOUNDARY_ARN')
        plan=base_plan();resource(plan,'aws_iam_role.function["consumer"]')['change']['after']['permissions_boundary']=None;self.assert_rejected(plan,'ROLE_BOUNDARY')

    def test_actual_worker_changes_cannot_hide_behind_idle_variables(self):
        for address,key,value,code in [('aws_lambda_function.function["consumer"]','reserved_concurrent_executions',4,'WORKER_RESERVATION'),('aws_lambda_function.function["replay"]','reserved_concurrent_executions',None,'WORKER_RESERVATION'),('aws_lambda_event_source_mapping.shipment["source"]','enabled',True,'MAPPING_IDLE'),('aws_lambda_event_source_mapping.shipment["replay"]','scaling_config',[{'maximum_concurrency':2}],'MAPPING_IDLE')]:
            plan=base_plan();resource(plan,address)['change']['after'][key]=value;self.assert_rejected(plan,code)

    def test_deletions_replacements_imports_and_drift_require_separate_review(self):
        for actions in [['delete'],['delete','create'],['create','delete'],['forget']]:
            plan=base_plan();plan['resource_changes'][0]['change']['actions']=actions;self.assert_rejected(plan,'DESTRUCTIVE_OR_UNSUPPORTED_ACTION')
        plan=base_plan();plan['resource_changes'][0]['change']['importing']={'id':'synthetic'};self.assert_rejected(plan,'IMPORT_REQUIRES_REVIEW')
        plan=base_plan();plan['resource_drift']=[{}];self.assert_rejected(plan,'DRIFT_REQUIRES_REVIEW')

    def test_inventory_rejects_extra_missing_duplicate_and_alternate_provider_resources(self):
        plan=base_plan();plan['resource_changes'].append({'address':'aws_kms_key.extra','mode':'managed','type':'aws_kms_key','provider_name':'registry.terraform.io/hashicorp/aws','change':{'actions':['create'],'after':{}}});self.assert_rejected(plan,'RESOURCE_INVENTORY')
        plan=base_plan();plan['resource_changes'].pop();self.assert_rejected(plan,'RESOURCE_INVENTORY')
        plan=base_plan();plan['resource_changes'].append(copy.deepcopy(plan['resource_changes'][0]));self.assert_rejected(plan,'DUPLICATE_RESOURCE')
        plan=base_plan();plan['resource_changes'][0]['provider_name']='registry.terraform.io/other/aws';self.assert_rejected(plan,'RESOURCE_PROVIDER')

    def test_storage_protection_and_known_encryption(self):
        for address,key,value,code in [('aws_s3_bucket.evidence','force_destroy',True,'EVIDENCE_BUCKET'),('aws_s3_bucket_public_access_block.evidence','block_public_policy',False,'BUCKET_PUBLIC_ACCESS'),('aws_s3_bucket_versioning.evidence','versioning_configuration',[],'BUCKET_VERSIONING'),('aws_s3_bucket_server_side_encryption_configuration.evidence','rule',[],'BUCKET_ENCRYPTION'),('aws_dynamodb_table.data["audit"]','deletion_protection_enabled',False,'TABLE_PROFILE'),('aws_dynamodb_table.data["business"]','ttl',[{'enabled':True}],'TABLE_TTL'),('aws_dynamodb_table.data["ledger"]','read_capacity',50,'TABLE_PROFILE'),('aws_sqs_queue.queue["source"]','sqs_managed_sse_enabled',None,'QUEUE_PROFILE')]:
            plan=base_plan();resource(plan,address)['change']['after'][key]=value;self.assert_rejected(plan,code)

    def test_authentication_and_client_settings(self):
        for address,key,value,code in [('aws_cognito_user_pool.operators','mfa_configuration','OFF','COGNITO_PROFILE'),('aws_cognito_user_pool_client.dashboard','generate_secret',True,'COGNITO_CLIENT'),('aws_apigatewayv2_route.authenticated','authorization_type','NONE','API_AUTH'),('aws_apigatewayv2_api.api','cors_configuration',[{'allow_origins':['*']}],'API_CORS')]:
            plan=base_plan();resource(plan,address)['change']['after'][key]=value;self.assert_rejected(plan,code)
        plan=base_plan();plan['variables']['callback_urls']['value']=['http://external.example/'];self.assert_rejected(plan,'CALLBACK_URLS')

    def test_wrong_names_unknown_settings_and_unexpected_log_retention(self):
        plan=base_plan();resource(plan,'aws_sns_topic.alerts')['change']['after']['name']='unrelated';self.assert_rejected(plan,'RESOURCE_NAME')
        plan=base_plan();resource(plan,'aws_cloudwatch_log_group.api')['change']['after']['retention_in_days']=30;self.assert_rejected(plan,'LOG_PROFILE')
        plan=base_plan();resource(plan,'aws_cognito_user_pool.operators')['change']['after']=None;self.assert_rejected(plan,'RESOURCE_VALUES')

    def test_malformed_structures_callbacks_and_provider_types_fail_closed(self):
        for changes in [{'variables':[]},{'resource_changes':None},{'resource_changes':[None]},{'errored':True}]:
            plan=base_plan();plan.update(changes);self.assertFalse(self.check(plan)['checks_passed'])
        for urls in [[],[1],['https://example.test:99999/'],['https://example.test/?code=private'],['https://user:pass@example.test/'],['https://exa mple.test/']]:
            plan=base_plan();plan['variables']['callback_urls']['value']=urls;self.assert_rejected(plan,'CALLBACK_URLS')
        plan=base_plan();plan['resource_changes'][0]['change']=None;self.assert_rejected(plan,'RESOURCE_VALUES')
        plan=base_plan();resource(plan,'aws_lambda_function.function["api"]')['change']['after']['reserved_concurrent_executions']=10;self.assert_rejected(plan,'FUNCTION_RESERVATION')

    def test_noop_and_update_actions_remain_supported(self):
        plan=base_plan()
        for i,r in enumerate(plan['resource_changes']):r['change']['actions']=['update'] if i%2 else ['no-op']
        self.assertTrue(self.check(plan)['checks_passed'])

    def test_notifications_are_counted_only_when_explicitly_configured(self):
        plan=base_plan();plan['variables']['budget_alert_email']['value']='alerts@example.test';self.assert_rejected(plan,'RESOURCE_INVENTORY')
        for address in ['aws_sns_topic_subscription.email[0]','aws_budgets_budget.sandbox[0]']:
            values={'protocol':'email','endpoint':'alerts@example.test'} if address.startswith('aws_sns') else {'name':'queuerescue-dev-monthly','budget_type':'COST','limit_unit':'USD','time_unit':'MONTHLY'}
            plan['resource_changes'].append({'address':address,'mode':'managed','type':address.split('.')[0],'provider_name':'registry.terraform.io/hashicorp/aws','change':{'actions':['create'],'after':values}})
        self.assertTrue(self.check(plan)['checks_passed'])
        resource(plan,'aws_sns_topic_subscription.email[0]')['change']['after']['endpoint']='other@example.test'
        self.assert_rejected(plan,'NOTIFICATION_PROFILE')

    def test_cli_hashes_input_and_saves_private_report_without_echoing_values(self):
        raw=json.dumps(base_plan(),indent=2).replace('\n','\r\n').encode()
        with patch('sys.argv',['review_aws_plan','--account-id',ACCOUNT,'--plan-json','private.json']),patch('tools.review_aws_plan.Path.read_bytes',return_value=raw),patch('tools.review_aws_plan.save_private_json') as save,patch('sys.stdout',new_callable=io.StringIO) as output:
            main()
        self.assertEqual(save.call_args.args[0],'.data/aws-plan-review.json');self.assertEqual(len(save.call_args.args[1]['plan_sha256']),64)
        self.assertEqual(save.call_args.args[1]['plan_sha256'],sha256(raw).hexdigest())
        self.assertNotIn(ACCOUNT,output.getvalue());self.assertFalse(save.call_args.args[1]['deployment_ready'])

    def test_cli_rejected_plan_exits_nonzero_and_bad_json_is_not_reported_as_success(self):
        with patch('sys.argv',['review_aws_plan','--account-id',ACCOUNT,'--plan-json','private.json']),patch('tools.review_aws_plan.Path.read_bytes',return_value=b'{}'),patch('tools.review_aws_plan.save_private_json'),patch('sys.stdout',new_callable=io.StringIO),self.assertRaises(SystemExit) as exit_result:main()
        self.assertEqual(exit_result.exception.code,2)
        with patch('sys.argv',['review_aws_plan','--account-id',ACCOUNT,'--plan-json','private.json']),patch('tools.review_aws_plan.Path.read_bytes',return_value=b'not json'),patch('tools.review_aws_plan.save_private_json') as save,patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):main()
        save.assert_not_called()
