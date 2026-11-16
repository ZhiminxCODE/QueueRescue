import io
import json
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cloud_support import AWS_TESTS_AVAILABLE, CloudFixture

if AWS_TESTS_AVAILABLE:
    import boto3
    from botocore.exceptions import ReadTimeoutError
    from cloud.api import route
    from cloud.aws import wire
    from cloud.collector import collect
    from cloud.contracts import CloudProblem, encode, request_identity
    from cloud.recovery import approve_plan, control_job, create_plan, job_detail, start_job, verify_job
    from cloud.replay import step
    from cloud.reports import report, MAX_READS
    from cloud.telemetry import message_refs, queue_arn, reference


FIXTURES = json.loads((Path(__file__).parent / 'fixtures/cloud_incidents.json').read_text())['cases']


@unittest.skipUnless(AWS_TESTS_AVAILABLE, 'Install requirements-aws-test.txt for offline AWS SDK tests')
class CloudReportTests(CloudFixture):
    def setUp(self):
        super().setUp()
        self.now = int(time.time()) + 30
        self.repo.clock = lambda: self.now
        self.operator, self.approver = {'id': 'operator-1'}, {'id': 'approver-1'}
        self.counter = 0

    def request(self, path, data, actor=None, key=None):
        self.counter += 1
        return request_identity(actor or self.operator, path, {'Idempotency-Key': key or 'report-test-' + str(self.counter)}, data)

    def plan(self, identifiers):
        data = {'messageIds': identifiers, 'rate': 25}
        return create_plan(self.repo, self.operator, data, self.request('/replay-plans', data))

    def job(self, plan):
        data = {'planHash': plan['planHash'], 'note': 'Synthetic evidence reviewed.'}
        approve_plan(self.repo, self.approver, plan['id'], data, self.request('/approve', data, self.approver))
        data = {'approvedPlanId': plan['id']}
        return start_job(self.repo, self.operator, data, self.request('/replay-jobs', data))

    def seed(self, fixture):
        for i, message in enumerate(fixture['messages']):
            body = json.loads(self.body)
            body.update(operationId=message.get('operation', 'shipment:invalid'), eventId='synthetic-' + str(i),
                        customerEmail='private@example.test', privateToken='private-token-value')
            attrs = {'correlationId': {'DataType': 'String', 'StringValue': 'private-correlation'}}
            if message.get('attributeConflict'):
                attrs['replayJobId'] = {'DataType': 'String', 'StringValue': 'private-conflict'}
            self.put_dlq(body='not-json-private-value' if message.get('invalid') else json.dumps(body), attributes=attrs)
        with redirect_stdout(io.StringIO()):
            identifiers = sorted(collect(self.repo)['indexed'])
        self.verifier.statuses.update(fixture['outcomes'])
        self.incident_id = self.repo.get(self.repo.queue_pk, 'MSG#' + identifiers[0])['incidentId']
        return identifiers

    def exercise(self, fixture):
        identifiers = self.seed(fixture)
        plan, job = None, None
        with redirect_stdout(io.StringIO()):
            if fixture['action'] != 'none':
                plan = self.plan(identifiers)
            if fixture['action'] in {'recover', 'uncertain', 'late_verify'}:
                job = self.job(plan)
                sqs = self.clients.sqs
                if fixture['action'] == 'uncertain':
                    original_send = sqs.send_message
                    def lost(**kwargs):
                        original_send(**kwargs)
                        raise ReadTimeoutError(endpoint_url='https://sqs.us-east-1.amazonaws.com')
                    with patch.object(sqs, 'send_message', side_effect=lost):
                        step(self.repo, job['id'], 0, sleep=lambda _: None)
                else:
                    step(self.repo, job['id'], 0, sleep=lambda _: None)
                    self.now += 5
                    if fixture['action'] == 'late_verify':
                        control_job(self.repo, self.operator, job['id'], 'cancel', {}, self.request('/cancel', {}))
                        self.now += 10
                    for operation in fixture['outcomes']:
                        self.verifier.statuses[operation] = 'COMPLETE'
                    verify_job(self.repo, self.operator, job['id'])
        return identifiers, plan, job

    def gateway(self, method='GET', path=None, data=None, group='Viewer', key='cost-request-001'):
        return {'rawPath': '/api' + (path or '/incidents/' + self.incident_id + '/report'),
            'headers': {'Idempotency-Key': key}, 'body': json.dumps(data or {}),
            'requestContext': {'http': {'method': method}, 'authorizer': {'jwt': {'claims': {
                'sub': group.lower() + '-1', 'token_use': 'access', 'cognito:groups': [group]}}}}}

    def test_labeled_incident_fixtures_reconcile_all_dispositions(self):
        for fixture in FIXTURES:
            # Isolate queue/ledger data between labeled cases.
            with self.subTest(case=fixture['id']):
                if fixture is not FIXTURES[0]:
                    self.tearDown_fixture()
                    self.setUp()
                self.exercise(fixture)
                result = report(self.repo, self.incident_id, {})
                self.assertEqual(result['counts'], fixture['expected'])
                self.assertEqual(sum(result['counts'].values()), result['coverage']['indexedSnapshotCount'])
                self.assertTrue(result['coverage']['completeForIndexedHistory'])
                self.assertLessEqual(result['coverage']['metadataReads'], MAX_READS)
                self.assertIsNone(result['cost']['measurement'])
                self.assertIsNone(result['cost']['incidentTotalUSD'])
                self.assertFalse(result['verification']['liveVerificationPerformed'])
                self.assertNotIn('private', json.dumps(result))

    def tearDown_fixture(self):
        self.doCleanups()

    def test_report_is_read_only_and_hash_is_stable_for_the_same_observations(self):
        self.exercise(FIXTURES[0])
        with patch.object(self.repo, 'commit', side_effect=AssertionError('Write')), \
             patch.object(self.clients.ddb, 'scan', side_effect=AssertionError('Scan')), \
             patch.object(self.clients.s3, 'get_object', side_effect=AssertionError('Payload')), \
             patch.object(self.clients.sqs, 'get_queue_attributes', side_effect=AssertionError('Queue read')), \
             patch.object(self.clients.sqs, 'receive_message', side_effect=AssertionError('Receive')), \
             patch.object(self.clients.sqs, 'send_message', side_effect=AssertionError('Replay')), \
             patch.object(self.verifier, 'invoke', side_effect=AssertionError('Live verification')):
            first = route(self.repo, self.gateway())
            second = route(self.repo, self.gateway())
        self.assertEqual(first, second)
        self.assertEqual(first['verification']['attemptedOperations'], 1)
        self.assertEqual(first['verification']['completeOperations'], 1)
        self.assertEqual(first['verification']['completePercent'], 100)
        self.assertEqual(len(first['reportHash']), 64)
        from cloud.contracts import sha
        browser_numbers = json.loads(encode(first), parse_float=lambda value: int(float(value)) if float(value).is_integer() else float(value))
        digest = browser_numbers.pop('reportHash')
        self.assertEqual(sha(encode(browser_numbers)), digest)

    def test_late_verification_records_resolution_without_moving_it_on_refresh(self):
        _, _, job = self.exercise(FIXTURES[5])
        first = report(self.repo, self.incident_id, {})
        self.assertEqual(first['jobs'][0]['state'], 'CANCELLED')
        self.assertEqual(first['duration']['resolvedDurationSeconds'], 15)
        self.now += 20
        verify_job(self.repo, self.operator, job['id'])
        second = report(self.repo, self.incident_id, {})
        self.assertEqual(first['duration']['resolvedAt'], second['duration']['resolvedAt'])
        self.assertEqual(second['duration']['resolvedDurationSeconds'], 15)

    def test_newer_conflicting_business_evidence_does_not_count_as_recovered(self):
        identifiers, _, _ = self.exercise(FIXTURES[0])
        self.now += 1
        self.verifier.statuses['shipment:o1'] = 'NOT_STARTED'
        plan = self.plan([identifiers[0]])
        job = self.job(plan)
        with redirect_stdout(io.StringIO()):
            step(self.repo, job['id'], 0, sleep=lambda _: None)
        self.now += 1
        self.verifier.statuses['shipment:o1'] = 'UNKNOWN'
        verify_job(self.repo, self.operator, job['id'])
        result = report(self.repo, self.incident_id, {})
        self.assertEqual(result['counts']['recovered'], 0)
        self.assertIn('CONFLICTING_DOMAIN_EVIDENCE', result['coverage']['issues'])

    def test_missing_and_legacy_metadata_are_partial_without_dropping_totals(self):
        identifiers, _, _ = self.exercise(FIXTURES[0])
        self.clients.ddb.delete_item(TableName=self.repo.table,
            Key=wire({'pk': 'INCIDENT#' + self.incident_id, 'sk': 'MSG#' + identifiers[0]}))
        result = report(self.repo, self.incident_id, {})
        self.assertFalse(result['coverage']['completeForIndexedHistory'])
        self.assertIn('SNAPSHOT_METADATA_MISSING', result['coverage']['issues'])
        self.assertEqual(sum(result['counts'].values()), 2)
        self.clients.ddb.update_item(TableName=self.repo.table,
            Key=wire({'pk': 'INCIDENT#' + self.incident_id, 'sk': 'MSG#' + identifiers[1]}),
            UpdateExpression='REMOVE reportIndexVersion')
        self.assertIn('LEGACY_HISTORY_NOT_INDEXED', report(self.repo, self.incident_id, {})['coverage']['issues'])

    def test_plan_tampering_and_missing_completion_proof_fail_closed(self):
        identifiers, plan, job = self.exercise(FIXTURES[0])
        attempt = job_detail(self.repo, job['id'])['attempts'][0]
        self.clients.ddb.update_item(TableName=self.repo.table, Key=wire({'pk': attempt['pk'], 'sk': attempt['sk']}),
            UpdateExpression='REMOVE checkedAt')
        result = report(self.repo, self.incident_id, {})
        self.assertEqual(result['counts']['recovered'], 0)
        self.clients.ddb.update_item(TableName=self.repo.table, Key=wire({'pk': 'PLAN#' + plan['id'], 'sk': 'META'}),
            UpdateExpression='SET planHash=:bad', ExpressionAttributeValues=wire({':bad': 'changed'}))
        result = report(self.repo, self.incident_id, {})
        self.assertIn('MISSING_OR_INVALID_PLAN', result['coverage']['issues'])
        self.assertEqual(result['counts']['recovered'], 0)

    def test_optional_correlated_logs_add_supported_findings_without_changing_counts(self):
        identifiers, _, _ = self.exercise(FIXTURES[1])
        member = self.repo.get(self.repo.queue_pk, 'MSG#' + identifiers[0])
        from dataclasses import replace
        self.repo.settings = replace(self.settings, consumer_log_group='/aws/lambda/qr-consumer')
        logs = boto3.client('logs', region_name=self.settings.region)
        self.clients.logs = logs
        logs.create_log_group(logGroupName='/aws/lambda/qr-consumer')
        stream = '2026/11/16/[$LATEST]report-test'
        logs.create_log_stream(logGroupName='/aws/lambda/qr-consumer', logStreamName=stream)
        row = {'telemetryVersion': 1, 'component': 'consumer', 'event': 'CONSUMER_FAILED', 'outcome': 'TIMEOUT_AFTER',
               'at': self.now * 1000, **message_refs(member['originalMessageId'], member['operationId']),
               'queueRef': reference('queue', queue_arn(self.settings.source_queue_url))}
        logs.put_log_events(logGroupName='/aws/lambda/qr-consumer', logStreamName=stream,
                            logEvents=[{'timestamp': row['at'], 'message': json.dumps(row)}])
        before = report(self.repo, self.incident_id, {})
        sampled = report(self.repo, self.incident_id, {'evidenceMessageId': identifiers[0]})
        self.assertEqual(before['counts'], sampled['counts'])
        self.assertEqual(sampled['hypotheses'][0]['code'], 'TIMEOUT_AFTER_OBSERVED')
        self.assertTrue(sampled['hypotheses'][0]['evidenceIds'])
        self.assertFalse(sampled['coverage']['logEvidenceCompleteForIncident'])
        self.assertTrue(any(e.get('logUrl') for e in sampled['evidence']))

    def test_report_auth_scope_and_invalid_queries_precede_external_reads(self):
        self.exercise(FIXTURES[2])
        event = self.gateway()
        event['requestContext'].pop('authorizer')
        with self.assertRaises(CloudProblem) as failure:
            route(self.repo, event)
        self.assertEqual(failure.exception.status, 401)
        for query in ({'logGroup': 'arbitrary'}, {'jobId': 'job-' + 'a' * 24}, {'evidenceMessageId': 'bad'}, {'evidenceMessageId': []}):
            with self.subTest(query=query), self.assertRaises(CloudProblem) as failure:
                report(self.repo, self.incident_id, query)
            self.assertEqual(failure.exception.status, 400)
        with self.assertRaises(CloudProblem) as failure:
            report(self.repo, self.incident_id, {'evidenceMessageId': 'f' * 64})
        self.assertEqual(failure.exception.status, 404)
        self.repo.queue_pk = 'QUEUE#other'
        with self.assertRaises(CloudProblem) as failure:
            report(self.repo, self.incident_id, {})
        self.assertEqual(failure.exception.status, 404)

    def cost(self, **changes):
        return {'kind': 'MEASURED', 'amountUSD': '0.0123', 'sourceKind': 'AWS_BILLING_EXPORT', 'sourceSha256': 'a' * 64,
                'periodStart': self.now - 60, 'periodEnd': self.now, 'observedAt': self.now, **changes}

    def test_cost_observations_require_admin_and_preserve_measurement_estimate_provenance(self):
        self.exercise(FIXTURES[2])
        path = '/incidents/' + self.incident_id + '/cost-observations'
        for group in ('Viewer', 'Operator', 'Approver'):
            with self.subTest(group=group), self.assertRaises(CloudProblem) as failure:
                route(self.repo, self.gateway('POST', path, self.cost(), group))
            self.assertEqual(failure.exception.status, 403)
        first = route(self.repo, self.gateway('POST', path, self.cost(), 'Admin'))
        repeated = route(self.repo, self.gateway('POST', path, self.cost(), 'Admin'))
        self.assertEqual(first, repeated)
        self.assertFalse(first['providerVerified'])
        with self.assertRaises(CloudProblem) as failure:
            route(self.repo, self.gateway('POST', path, self.cost(amountUSD='0'), 'Admin'))
        self.assertEqual(failure.exception.status, 409)
        self.now += 1
        estimate = self.cost(kind='ESTIMATED', sourceKind='OFFLINE_CALCULATOR', amountUSD='0.20')
        route(self.repo, self.gateway('POST', path, estimate, 'Admin', key='cost-estimate-002'))
        result = report(self.repo, self.incident_id, {})
        self.assertEqual(result['cost']['measurement']['amountUSD'], '0.0123')
        self.assertEqual(result['cost']['estimate']['amountUSD'], '0.20')
        self.assertIsNone(result['cost']['incidentTotalUSD'])
        self.assertNotIn('admin-1', json.dumps(result))

    def test_invalid_cost_payloads_never_record_a_measurement(self):
        self.exercise(FIXTURES[2])
        path = '/incidents/' + self.incident_id + '/cost-observations'
        for changes in ({'amountUSD': '-1'}, {'amountUSD': 0}, {'amountUSD': 'NaN'}, {'kind': []},
                        {'sourceSha256': 'private-token'}, {'sourceKind': 'MANUAL_ESTIMATE'},
                        {'observedAt': self.now + 1}, {'periodStart': True}, {'unknown': 'private'}):
            with self.subTest(changes=changes), self.assertRaises(CloudProblem) as failure:
                route(self.repo, self.gateway('POST', path, self.cost(**changes), 'Admin'))
            self.assertEqual(failure.exception.status, 400)
        self.assertIsNone(report(self.repo, self.incident_id, {})['cost']['measurement'])

    def test_index_writes_are_atomic_and_expire_with_the_incident_not_reservations(self):
        identifiers = self.seed(FIXTURES[0])
        with patch.object(self.repo, 'commit', side_effect=RuntimeError('Interrupted')):
            with self.assertRaises(RuntimeError):
                self.plan(identifiers)
        self.assertEqual(self.repo.query('INCIDENT#' + self.incident_id, 'PLAN#')[0], [])
        plan = self.plan(identifiers)
        job = self.job(plan)
        for kind in ('PLAN#', 'JOB#'):
            links = self.repo.query('INCIDENT#' + self.incident_id, kind)[0]
            self.assertEqual(len(links), 1)
            self.assertEqual(links[0]['expires_at'], self.repo.get(self.repo.queue_pk, 'INC#' + self.incident_id)['expires_at'])
        from cloud.recovery import lock_key
        lock = self.repo.get(**lock_key(self.repo, 'shipment:o1'))
        self.assertNotIn('expires_at', lock)
        self.assertEqual(job_detail(self.repo, job['id'])['counts']['QUEUED'], 1)

    def test_bounded_plan_and_member_reads_mark_the_report_partial(self):
        identifiers = self.seed(FIXTURES[0])
        for _ in range(6):
            self.plan(identifiers)
        result = report(self.repo, self.incident_id, {})
        self.assertIn('PLAN_LIMIT_REACHED', result['coverage']['issues'])
        self.assertEqual(result['coverage']['plansRead'], 5)
        self.assertLessEqual(result['coverage']['metadataReads'], MAX_READS)
        original_query = self.repo.query
        def partial_page(pk, prefix, **kwargs):
            rows, cursor = original_query(pk, prefix, **kwargs)
            return (rows, {'pk': pk, 'sk': rows[-1]['sk']}) if prefix == 'MSG#' else (rows, cursor)
        with patch.object(self.repo, 'query', side_effect=partial_page):
            self.assertIn('MEMBER_LIMIT_REACHED', report(self.repo, self.incident_id, {})['coverage']['issues'])

    def test_concurrent_job_change_does_not_produce_completion_counts(self):
        _, _, job = self.exercise(FIXTURES[0])
        original_get, seen = self.repo.get, 0
        def changed(pk, *args, **kwargs):
            nonlocal seen
            row = original_get(pk, *args, **kwargs)
            if pk == 'JOB#' + job['id']:
                seen += 1
                if seen == 2:
                    return {**row, 'updatedAt': row['updatedAt'] + 1}
            return row
        with patch.object(self.repo, 'get', side_effect=changed):
            result = report(self.repo, self.incident_id, {})
        self.assertEqual(result['counts']['recovered'], 0)
        self.assertIn('JOB_CHANGED_OR_ATTEMPTS_TRUNCATED', result['coverage']['issues'])

    def test_mixed_incident_plan_does_not_leak_members_or_attach_unrelated_jobs(self):
        identifiers = self.seed({'messages': [{'operation': 'shipment:o1'}, {'invalid': True}],
                                 'outcomes': {'shipment:o1': 'NOT_STARTED'}})
        plan = self.plan(identifiers)
        self.job(plan)
        members = [self.repo.get(self.repo.queue_pk, 'MSG#' + identifier) for identifier in identifiers]
        invalid = next(member for member in members if not member['valid'])
        valid = next(member for member in members if member['valid'])
        result = report(self.repo, invalid['incidentId'], {})
        self.assertEqual(result['counts'], {'recovered': 0, 'skipped': 0, 'blocked': 1, 'unresolved': 0})
        self.assertEqual(result['coverage']['jobsRead'], 0)
        self.assertNotIn(valid['id'], json.dumps(result))
        self.assertIsNone(result['duration']['startedAt'])

    def test_maximum_25_message_plan_and_job_still_fit_one_transaction(self):
        from dataclasses import replace
        from cloud.contracts import sha
        from cloud.investigation import incident_writes
        identifiers, incidents = [], []
        for i in range(25):
            self.repo.settings = replace(self.settings, consumer_version='synthetic-version-' + str(i))
            body = json.loads(self.body)
            operation = 'shipment:bounded-' + str(i)
            body.update(operationId=operation, eventId='event-' + str(i))
            body = json.dumps(body)
            identifier = sha('synthetic-message-' + str(i))
            key = 'evidence/' + identifier + '.json'
            snapshot = {'body': body, 'queueUrl': self.settings.dlq_url, 'checksum': sha(body),
                        'messageAttributes': {}, 'systemAttributes': {}, 'originalMessageId': 'source-' + str(i)}
            row = {'pk': self.repo.queue_pk, 'sk': 'MSG#' + identifier, 'id': identifier,
                   'operationId': operation, 'valid': True, 'observedAt': self.now,
                   'expires_at': self.now + 7 * 86400, 'checksum': sha(body), 'evidenceKey': key,
                   'evidenceChecksum': self.repo.put_evidence(key, snapshot)}
            self.repo.commit(incident_writes(self.repo, row), 'fixture', 'FIXTURE', identifier)
            identifiers.append(identifier)
            incidents.append(row['incidentId'])
            self.verifier.statuses[operation] = 'NOT_STARTED'
        with patch.object(self.clients.ddb, 'transact_write_items', wraps=self.clients.ddb.transact_write_items) as transaction:
            plan = self.plan(identifiers)
            job = self.job(plan)
        self.assertTrue(all(len(call.kwargs['TransactItems']) <= 100 for call in transaction.call_args_list))
        self.assertEqual(max(len(call.kwargs['TransactItems']) for call in transaction.call_args_list), 80)
        self.assertEqual(len(job_detail(self.repo, job['id'])['attempts']), 25)
        self.assertTrue(all(len(self.repo.query('INCIDENT#' + incident, 'JOB#')[0]) == 1 for incident in incidents))
