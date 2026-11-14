import io
import json
import unittest

from cloud_support import AWS_TESTS_AVAILABLE, CloudFixture

if AWS_TESTS_AVAILABLE:
    from botocore.exceptions import ReadTimeoutError
    from cloud.api import route
    from cloud.collector import collect
    from cloud.contracts import CloudProblem, encode, request_identity
    from cloud.recovery import approve_plan, control_job, create_plan, job_detail, start_job, verify_job
    from cloud.replay import step, worker_recovery


class FakeStates:
    class AlreadyExists(Exception):
        pass

    class exceptions:
        pass

    def __init__(self):
        self.executions = {}
        self.exceptions.ExecutionAlreadyExists = self.AlreadyExists

    def start_execution(self, **kwargs):
        self.executions.setdefault(kwargs['name'], kwargs)
        return {'executionArn':kwargs['stateMachineArn'].replace(':stateMachine:', ':execution:') + ':' + kwargs['name']}


@unittest.skipUnless(AWS_TESTS_AVAILABLE, 'Install requirements-aws-test.txt for offline AWS SDK tests')
class AwsRecoveryTests(CloudFixture):
    def setUp(self):
        super().setUp()
        self.clients.states = FakeStates()
        self.operator, self.approver = {'id':'operator-1'}, {'id':'approver-1'}
        self.counter = 0
        self.put_dlq()
        self.identifiers = collect(self.repo)['indexed']
        self.verifier.statuses['shipment:o1'] = 'NOT_STARTED'

    def request(self, actor, path, data):
        self.counter += 1
        return request_identity(actor, path, {'Idempotency-Key':'request-' + str(self.counter).zfill(4)}, data)

    def plan(self):
        data = {'messageIds':self.identifiers,'rate':25}
        return create_plan(self.repo, self.operator, data, self.request(self.operator, '/replay-plans', data))

    def approve(self, plan, actor=None, plan_hash=None):
        actor = actor or self.approver
        data = {'planHash':plan_hash or plan['planHash'],'note':'Business outcomes reviewed.'}
        return approve_plan(self.repo, actor, plan['id'], data, self.request(actor, '/approve', data))

    def job(self):
        plan = self.plan()
        self.approve(plan)
        data = {'approvedPlanId':plan['id']}
        return start_job(self.repo, self.operator, data, self.request(self.operator, '/replay-jobs', data))

    def run_step(self, job, **kwargs):
        return step(self.repo, job['id'], job['generation'], sleep=lambda seconds: None, **kwargs)

    def test_full_aws_plan_approval_send_and_business_verification(self):
        job = self.job()
        self.assertFalse(self.run_step(job)['continue'])
        current = job_detail(self.repo, job['id'])
        self.assertEqual(current['state'], 'VERIFYING')
        self.assertEqual(current['counts']['SENT'], 1)
        messages = self.clients.sqs.receive_message(QueueUrl=self.settings.replay_queue_url, MessageAttributeNames=['All'])['Messages']
        self.assertEqual(messages[0]['Body'].encode(), self.body.encode())
        self.assertEqual(messages[0]['MessageAttributes']['replayJobId']['StringValue'], job['id'])
        self.assertEqual(verify_job(self.repo, self.operator, job['id'])['state'], 'PARTIAL')
        self.verifier.statuses['shipment:o1'] = 'COMPLETE'
        verified = verify_job(self.repo, self.operator, job['id'])
        self.assertEqual(verified['state'], 'COMPLETED')
        self.assertEqual(verified['counts']['VERIFIED'], 1)

    def test_unknown_and_completed_operations_are_excluded(self):
        for status, expected in [('UNKNOWN','REVIEW'),('IN_PROGRESS','REVIEW'),('COMPLETE','SKIP')]:
            self.verifier.statuses['shipment:o1'] = status
            plan = self.plan()
            self.assertEqual(plan['document']['decisions'][0]['decision'], expected)
            with self.assertRaises(CloudProblem):
                self.approve(plan)

    def test_approval_requires_another_person_and_exact_hash(self):
        plan = self.plan()
        for actor, hash_value in [(self.operator, None), (self.approver, 'changed')]:
            with self.assertRaises(CloudProblem):
                self.approve(plan, actor, hash_value)

    def test_an_operation_cannot_be_reserved_by_two_jobs(self):
        one, two = self.plan(), self.plan()
        self.approve(one)
        self.approve(two)
        data = {'approvedPlanId':one['id']}
        start_job(self.repo, self.operator, data, self.request(self.operator, '/jobs', data))
        data = {'approvedPlanId':two['id']}
        with self.assertRaises(CloudProblem):
            start_job(self.repo, self.operator, data, self.request(self.operator, '/jobs', data))
        catalog, _ = self.repo.query('CATALOG#JOB', 'job-')
        self.assertEqual(len(catalog), 1)

    def test_business_state_is_rechecked_immediately_before_send(self):
        job = self.job()
        self.verifier.statuses['shipment:o1'] = 'COMPLETE'
        self.run_step(job)
        self.assertEqual(job_detail(self.repo, job['id'])['counts']['SKIPPED'], 1)
        self.assertNotIn('Messages', self.clients.sqs.receive_message(QueueUrl=self.settings.replay_queue_url))

    def test_send_timeout_after_acceptance_never_triggers_a_second_send(self):
        job = self.job()
        sqs = self.clients.sqs
        class LostAcknowledgment:
            def send_message(self, **kwargs):
                sqs.send_message(**kwargs)
                raise ReadTimeoutError(endpoint_url='https://sqs.us-east-1.amazonaws.com')
        self.clients.sqs = LostAcknowledgment()
        self.run_step(job)
        self.assertEqual(job_detail(self.repo, job['id'])['counts']['UNCERTAIN'], 1)
        self.assertEqual(job_detail(self.repo, job['id'])['state'], 'PAUSED')
        self.clients.sqs = sqs
        self.verifier.statuses['shipment:o1'] = 'COMPLETE'
        verify_job(self.repo, self.operator, job['id'])
        resumed = control_job(self.repo, self.operator, job['id'], 'resume', {}, self.request(self.operator, '/resume', {}))
        self.run_step(resumed)
        self.assertEqual(verify_job(self.repo, self.operator, job['id'])['state'], 'COMPLETED')
        self.assertEqual(len(sqs.receive_message(QueueUrl=self.settings.replay_queue_url, MaxNumberOfMessages=10)['Messages']), 1)

    def test_worker_crash_before_ledger_ack_requires_reconciliation(self):
        job = self.job()
        def crash():
            raise RuntimeError('Worker stopped after send')
        with self.assertRaises(RuntimeError):
            self.run_step(job, after_send=crash)
        self.assertEqual(job_detail(self.repo, job['id'])['counts']['LEASED'], 1)
        worker_recovery(self.repo, job['id'], job['generation'])
        self.assertEqual(job_detail(self.repo, job['id'])['counts']['UNCERTAIN'], 1)
        self.assertEqual(verify_job(self.repo, self.operator, job['id'])['counts']['REVIEW'], 1)
        self.run_step(job)
        self.assertEqual(len(self.clients.sqs.receive_message(QueueUrl=self.settings.replay_queue_url, MaxNumberOfMessages=10)['Messages']), 1)

    def test_expired_plan_stops_scheduling(self):
        job = self.job()
        self.repo.clock = lambda: 10 ** 12
        self.run_step(job)
        self.assertEqual(job_detail(self.repo, job['id'])['state'], 'PAUSED')
        self.assertNotIn('Messages', self.clients.sqs.receive_message(QueueUrl=self.settings.replay_queue_url))

    def gateway(self, path, data=None, group='Operator', key='client-request-123'):
        return {'rawPath':'/api' + path,'requestContext':{'http':{'method':'POST' if data is not None else 'GET'},
            'authorizer':{'jwt':{'claims':{'sub':group + '-person','token_use':'access','cognito:groups':group}}}},
            'headers':{'Idempotency-Key':key},'body':json.dumps(data or {})}

    def test_api_idempotent_plan_creation_and_payload_change_rejection(self):
        request = self.gateway('/replay-plans', {'messageIds':self.identifiers})
        one = route(self.repo, request)
        self.assertEqual(route(self.repo, request), one)
        changed = self.gateway('/replay-plans', {'messageIds':self.identifiers,'rate':2})
        with self.assertRaises(CloudProblem):
            route(self.repo, changed)

    def test_viewer_cannot_collect_or_create_plans(self):
        for path, body in [('/collections',{'acknowledgeVisibilityChange':True}),('/replay-plans',{'messageIds':self.identifiers})]:
            with self.assertRaises(CloudProblem) as error:
                route(self.repo, self.gateway(path, body, group='Viewer'))
            self.assertEqual(error.exception.status, 403)

    def test_only_explicit_allowlisted_small_selections_are_permitted(self):
        for data in [{'messageIds':[]},{'messageIds':self.identifiers * 26},{'messageIds':self.identifiers,'destination':'other'},{'messageIds':self.identifiers,'rate':True}]:
            with self.assertRaises(CloudProblem):
                create_plan(self.repo, self.operator, data, self.request(self.operator, '/plans', data))

    def test_cancel_preserves_sent_events_for_verification(self):
        job = self.job()
        self.run_step(job, max_sends=1)
        control_job(self.repo, self.operator, job['id'], 'cancel', {}, self.request(self.operator, '/cancel', {}))
        self.verifier.statuses['shipment:o1'] = 'COMPLETE'
        result = verify_job(self.repo, self.operator, job['id'])
        self.assertEqual(result['state'], 'CANCELLED')
        self.assertEqual(result['counts']['VERIFIED'], 1)

    def test_old_execution_generation_cannot_schedule_more_work(self):
        job = self.job()
        control_job(self.repo, self.operator, job['id'], 'pause', {}, self.request(self.operator, '/pause', {}))
        resumed = control_job(self.repo, self.operator, job['id'], 'resume', {}, self.request(self.operator, '/resume', {}))
        self.assertGreater(resumed['generation'], job['generation'])
        self.assertFalse(self.run_step(job)['continue'])
        self.assertNotIn('Messages', self.clients.sqs.receive_message(QueueUrl=self.settings.replay_queue_url))
