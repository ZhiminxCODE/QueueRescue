import io
import json
import os
import time
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from cloud_support import AWS_TESTS_AVAILABLE, CloudFixture
from cloud.telemetry import emit, message_refs, queue_arn, record_refs, reference, sanitized

if AWS_TESTS_AVAILABLE:
    import boto3
    from botocore.exceptions import ClientError, ReadTimeoutError
    from botocore.stub import Stubber
    from cloud.api import route
    from cloud.aws import wire
    from cloud.collector import collect
    from cloud.contracts import CloudProblem
    from cloud.log_evidence import lookup
    from cloud.shipment import consumer, verifier


class TelemetryTests(unittest.TestCase):
    def test_emission_allowlists_fields_and_hashes_sensitive_identity(self):
        refs = message_refs('source-message', 'private@example.test', {'correlationId': {'StringValue': 'secret-token\n'}})
        output = io.StringIO()
        with redirect_stdout(output):
            emit('consumer', 'CONSUMER_FAILED', clock=lambda: 1800000000, outcome='TIMEOUT_BEFORE',
                 body='private payload', exception='sensitive traceback', **refs)
        row = json.loads(output.getvalue())
        self.assertEqual(row['operationRef'], reference('operation', 'private@example.test'))
        self.assertEqual(row['correlationRef'], reference('correlation', 'secret-token\n'))
        self.assertNotIn('private', output.getvalue())
        self.assertNotIn('secret', output.getvalue())
        self.assertNotIn('body', row)
        self.assertEqual(len(output.getvalue().splitlines()), 1)

    def test_malformed_fields_and_logging_failure_cannot_change_processing(self):
        self.assertIsNone(reference('operation', '\ud800'))
        for row in ({}, {'telemetryVersion': 1, 'component': [], 'event': 'bad'},
                    {'telemetryVersion': 1, 'component': 'consumer', 'event': {}, 'at': 1}):
            self.assertIsNone(sanitized(row))
        with patch('builtins.print', side_effect=OSError('disk unavailable')):
            emit('consumer', 'CONSUMER_FAILED', outcome='UNKNOWN')

    def test_spoofed_source_replay_attributes_are_not_correlation_authority(self):
        source = 'arn:aws:sqs:us-east-1:123456789012:source'
        replay = source.replace(':source', ':replay')
        record = {'messageId': 'm1', 'body': '{}', 'eventSourceARN': source,
                  'messageAttributes': {'replayJobId': {'stringValue': 'job-spoof'}, 'replayAttemptId': {'stringValue': 'attempt-spoof'}}}
        self.assertNotIn('attemptRef', record_refs(record, replay))
        record['eventSourceARN'] = replay
        self.assertEqual(record_refs(record, replay)['attemptRef'], reference('attempt', 'attempt-spoof'))


@unittest.skipUnless(AWS_TESTS_AVAILABLE, 'Install requirements-aws-test.txt for offline AWS SDK tests')
class LogEvidenceTests(CloudFixture):
    def setUp(self):
        super().setUp()
        self.now = int(time.time()) + 10
        self.repo.clock = lambda: self.now
        self.repo.settings = replace(self.settings, consumer_log_group='/aws/lambda/qr-consumer', replay_log_group='/aws/lambda/qr-replay')
        self.logs = boto3.client('logs', region_name=self.settings.region)
        self.clients.logs = self.logs
        self.put_dlq(attributes={'correlationId': {'DataType': 'String', 'StringValue': 'private-correlation@example.test'}})
        with redirect_stdout(io.StringIO()):
            self.identifier = collect(self.repo)['indexed'][0]
        self.message = self.repo.get(self.repo.queue_pk, 'MSG#' + self.identifier)
        self.refs = {**message_refs(self.message['originalMessageId'], self.message['operationId']),
                     'queueRef': reference('queue', queue_arn(self.settings.source_queue_url))}

    def event(self, event='CONSUMER_FAILED', outcome='TIMEOUT_BEFORE', component='consumer', **fields):
        row = {'telemetryVersion': 1, 'component': component, 'event': event, 'outcome': outcome,
               'at': (self.now - 5) * 1000, **self.refs, **fields}
        return {'eventId': 'event-' + event, 'timestamp': row['at'],
                'message': json.dumps(row), 'logStreamName': '2026/11/16/[$LATEST]abc123'}

    def gateway(self, query=None, authenticated=True):
        return {'rawPath': '/api/messages/' + self.identifier + '/evidence', 'queryStringParameters': query or {},
                'requestContext': {'http': {'method': 'GET'}, **({'authorizer': {'jwt': {'claims': {
                    'sub': 'viewer', 'token_use': 'access', 'cognito:groups': ['Viewer']}}}} if authenticated else {})}}

    def test_sdk_shape_is_exactly_scoped_and_returns_allowlisted_evidence(self):
        event = self.event(body='private payload', error='private exception', token='private-token')
        with Stubber(self.logs) as stub:
            from cloud.log_evidence import context_for
            _, _, expected, _, start, end, _ = context_for(self.repo, self.identifier, {})
            pattern = '{ ' + ' && '.join(f'$.{k} = {json.dumps(v)}' for k, v in expected.items()) + ' }'
            stub.add_response('filter_log_events', {'events': [event]}, {
                'logGroupName': '/aws/lambda/qr-consumer', 'startTime': start, 'endTime': end,
                'filterPattern': pattern, 'limit': 25, 'unmask': False})
            result = route(self.repo, self.gateway())
            stub.assert_no_pending_responses()
        self.assertEqual(result['status'], 'FOUND')
        self.assertTrue(result['failureObserved'])
        self.assertTrue(result['searchComplete'])
        self.assertEqual(result['businessVerification'], 'NOT_ASSESSED')
        self.assertEqual(result['traceStatus'], 'NOT_INSTRUMENTED')
        self.assertNotIn('private', json.dumps(result))
        self.assertNotIn('body', result['items'][0])
        self.assertIn('.console.aws.amazon.com/cloudwatch/', result['items'][0]['logUrl'])
        self.assertIn('$252Faws$252Flambda$252Fqr-consumer', result['items'][0]['logUrl'])

    def test_empty_page_continues_and_duplicates_are_removed(self):
        event = self.event()
        with patch.object(self.logs, 'filter_log_events', side_effect=[
                {'events': [], 'nextToken': 'page-two'}, {'events': [event, event]}]) as filtered:
            result = lookup(self.repo, self.identifier, {})
        self.assertEqual(filtered.call_count, 2)
        self.assertEqual(filtered.call_args.kwargs['nextToken'], 'page-two')
        self.assertEqual(len(result['items']), 1)
        self.assertTrue(result['searchComplete'])

    def test_page_budget_and_repeated_tokens_are_inconclusive(self):
        with patch.object(self.logs, 'filter_log_events', return_value={'events': [], 'nextToken': 'same'}) as filtered:
            result = lookup(self.repo, self.identifier, {})
        self.assertEqual(filtered.call_count, 2)
        self.assertEqual(result['status'], 'INCONCLUSIVE')
        self.assertFalse(result['searchComplete'])
        self.assertEqual(result['sources'][0]['status'], 'TRUNCATED')
        self.assertNotIn('same', json.dumps(result))

    def test_denied_missing_and_transport_errors_never_become_success_or_leak_text(self):
        for code, status in [('AccessDeniedException', 'DENIED'), ('ResourceNotFoundException', 'MISSING'), ('ThrottlingException', 'UNAVAILABLE')]:
            failure = ClientError({'Error': {'Code': code, 'Message': 'private-secret'}}, 'FilterLogEvents')
            with self.subTest(code=code), patch.object(self.logs, 'filter_log_events', side_effect=failure):
                result = lookup(self.repo, self.identifier, {})
            self.assertEqual(result['sources'][0]['status'], status)
            self.assertEqual(result['status'], 'INCONCLUSIVE')
            self.assertFalse(result['failureObserved'])
            self.assertNotIn('private-secret', json.dumps(result))
        with patch.object(self.logs, 'filter_log_events', side_effect=ReadTimeoutError(endpoint_url='private')):
            self.assertEqual(lookup(self.repo, self.identifier, {})['sources'][0]['status'], 'UNAVAILABLE')

    def test_wrong_refs_components_and_unstructured_logs_cannot_be_evidence(self):
        bad_json = {**self.event(), 'message': 'private raw traceback'}
        other = self.event(messageRef=reference('message', 'other'))
        wrong_queue = self.event(queueRef=reference('queue', 'other'))
        wrong_component = self.event(event='API_COMPLETED', component='api')
        malformed = {**self.event(), 'message': json.dumps({'telemetryVersion': 1, 'component': [], 'event': []})}
        with patch.object(self.logs, 'filter_log_events', return_value={'events': [bad_json, other, wrong_queue, wrong_component, malformed]}):
            result = lookup(self.repo, self.identifier, {})
        self.assertEqual(result['items'], [])
        self.assertEqual(result['status'], 'INCONCLUSIVE')

    def test_rejects_unscoped_queries_bad_windows_unauthenticated_and_other_queue_before_logs(self):
        with patch.object(self.logs, 'filter_log_events', side_effect=AssertionError('Unscoped query')):
            for query in ({'logGroup': 'other'}, {'filterPattern': '*'}, {'from': '-1'}, {'from': str(self.now), 'to': '1'},
                          {'to': str(self.now + 1)}, {'from': 'NaN'}, {'from': []}, {'attemptId': 'spoof'}):
                with self.subTest(query=query), self.assertRaises(CloudProblem):
                    lookup(self.repo, self.identifier, query)
            with self.assertRaises(CloudProblem) as failure:
                route(self.repo, self.gateway(authenticated=False))
            self.assertEqual(failure.exception.status, 401)
            original_pk = self.repo.queue_pk
            self.repo.queue_pk = 'QUEUE#other'
            with self.assertRaises(CloudProblem) as failure:
                lookup(self.repo, self.identifier, {})
            self.assertEqual(failure.exception.status, 404)
            self.repo.queue_pk = original_pk

    def test_expiry_and_absent_configuration_prevent_queries(self):
        self.repo.settings = replace(self.repo.settings, consumer_log_group='')
        with patch.object(self.logs, 'filter_log_events', side_effect=AssertionError('Must not query')):
            result = lookup(self.repo, self.identifier, {})
            self.assertEqual(result['sources'][0]['status'], 'NOT_CONFIGURED')
            self.now = self.message['expires_at']
            with self.assertRaises(CloudProblem) as failure:
                lookup(self.repo, self.identifier, {})
            self.assertEqual(failure.exception.status, 404)

    def test_old_source_window_is_clipped_and_custom_window_is_bounded(self):
        lower = self.now - 2 * 86400
        self.clients.ddb.update_item(TableName=self.repo.table,
            Key=wire({'pk': self.repo.queue_pk, 'sk': 'MSG#' + self.identifier}),
            UpdateExpression='SET sourceSentAt=:sent', ExpressionAttributeValues=wire({':sent': lower}))
        with patch.object(self.logs, 'filter_log_events', return_value={'events': []}):
            result = lookup(self.repo, self.identifier, {})
            self.assertTrue(result['windowClipped'])
            self.assertFalse(result['searchComplete'])
            self.assertEqual(result['to'] - result['from'], 86400)
            earlier = lookup(self.repo, self.identifier, {'from': str(lower), 'to': str(lower + 300)})
            self.assertEqual(earlier['from'], lower)
            with self.assertRaises(CloudProblem):
                lookup(self.repo, self.identifier, {'from': str(lower), 'to': str(self.now)})

    def attempt(self):
        job_id, attempt_id = 'job-' + 'a' * 24, 'attempt-' + 'b' * 24
        plan = {'pk': 'PLAN#plan-test', 'sk': 'META', 'document': {'destination': self.settings.replay_queue_url,
            'decisions': [{'messageId': self.identifier, 'checksum': self.message['checksum'], 'evidenceChecksum': self.message['evidenceChecksum']}]}}
        job = {'pk': 'JOB#' + job_id, 'sk': 'META', 'planId': 'plan-test', 'createdAt': self.now - 60}
        attempt = {'pk': job['pk'], 'sk': 'ATTEMPT#' + self.identifier, 'id': attempt_id, 'operationId': self.message['operationId']}
        for row in (plan, job, attempt):
            self.clients.ddb.put_item(TableName=self.repo.table, Item=wire(row))
        return {'jobId': job_id, 'attemptId': attempt_id}

    def test_replay_queries_bind_attempt_job_operation_and_queue_and_keep_partial_evidence(self):
        query = self.attempt()
        refs = {'queueRef': reference('queue', queue_arn(self.settings.replay_queue_url)),
                'attemptRef': reference('attempt', query['attemptId']), 'jobRef': reference('job', query['jobId'])}
        accepted = self.event(event='REPLAY_SEND_ACCEPTED', component='replay', **refs)
        returned = self.event(event='CONSUMER_SUCCEEDED', outcome='HANDLER_RETURNED', **refs)
        failure = ClientError({'Error': {'Code': 'AccessDeniedException'}}, 'FilterLogEvents')
        with patch.object(self.logs, 'filter_log_events', side_effect=[failure, {'events': [accepted]}]) as filtered:
            result = lookup(self.repo, self.identifier, query)
        self.assertEqual(filtered.call_count, 2)
        self.assertEqual(len(result['items']), 1)
        self.assertFalse(result['searchComplete'])
        self.assertFalse(result['failureObserved'])
        self.assertEqual(result['businessVerification'], 'NOT_ASSESSED')
        with patch.object(self.logs, 'filter_log_events', side_effect=[{'events': [returned]}, {'events': [accepted]}]):
            self.assertEqual(len(lookup(self.repo, self.identifier, query)['items']), 2)
        with patch.object(self.logs, 'filter_log_events', side_effect=AssertionError('Wrong attempt')):
            with self.assertRaises(CloudProblem) as failure:
                lookup(self.repo, self.identifier, {**query, 'attemptId': 'attempt-' + 'c' * 24})
            self.assertEqual(failure.exception.status, 404)

    def test_real_consumer_failure_logs_correlate_without_exposing_payload(self):
        self.business()
        source = queue_arn(self.settings.source_queue_url)
        replay = queue_arn(self.settings.replay_queue_url)
        record = {'messageId': self.message['originalMessageId'], 'body': self.body,
                  'eventSourceARN': source, 'messageAttributes': {'correlationId': {'stringValue': 'private-correlation@example.test'}}}
        output = io.StringIO()
        with patch.dict(os.environ, {'QR_BUSINESS_TABLE': self.settings.business_table, 'QR_REPLAY_QUEUE_ARN': replay,
                                     'AWS_DEFAULT_REGION': self.settings.region}), redirect_stdout(output):
            result = consumer({'Records': [record]}, SimpleNamespace(aws_request_id='request-test'))
        self.assertEqual(result['batchItemFailures'], [{'itemIdentifier': self.message['originalMessageId']}])
        row = json.loads(output.getvalue())
        self.assertEqual(row['outcome'], 'TIMEOUT_BEFORE')
        self.assertEqual(row['messageRef'], self.refs['messageRef'])
        self.assertEqual(row['queueRef'], self.refs['queueRef'])
        self.assertNotIn('private-correlation', output.getvalue())
        event = {**self.event(), 'timestamp': row['at'], 'message': output.getvalue()}
        # Exercise the actual AWS SDK against an offline CloudWatch log group.
        self.logs.create_log_group(logGroupName='/aws/lambda/qr-consumer')
        self.logs.create_log_stream(logGroupName='/aws/lambda/qr-consumer', logStreamName=event['logStreamName'])
        self.logs.put_log_events(logGroupName='/aws/lambda/qr-consumer', logStreamName=event['logStreamName'],
                                 logEvents=[{'timestamp': row['at'], 'message': output.getvalue()}])
        evidence = lookup(self.repo, self.identifier, {})
        self.assertTrue(evidence['failureObserved'])
        self.assertEqual(evidence['items'][0]['outcome'], 'TIMEOUT_BEFORE')

    def test_logging_failure_cannot_retry_a_completed_business_effect(self):
        self.business()
        replay = queue_arn(self.settings.replay_queue_url)
        record = {'messageId': 'replay-id', 'body': self.body, 'eventSourceARN': replay, 'messageAttributes': {}}
        with patch.dict(os.environ, {'QR_BUSINESS_TABLE': self.settings.business_table,
                                     'QR_REPLAY_QUEUE_ARN': replay, 'AWS_DEFAULT_REGION': self.settings.region}), \
             patch('builtins.print', side_effect=OSError('log transport failed')):
            first = consumer({'Records': [record]}, None)
            shipment = self.repo.get('OP#shipment:o1', table=self.settings.business_table)['shipmentId']
            second = consumer({'Records': [record]}, None)
        self.assertEqual(first['batchItemFailures'], [])
        self.assertEqual(second['batchItemFailures'], [])
        self.assertEqual(self.repo.get('OP#shipment:o1', table=self.settings.business_table)['shipmentId'], shipment)

    def test_timeout_after_effect_is_failure_telemetry_even_when_business_completed(self):
        self.business()
        record = {'messageId': 'source-id', 'body': self.body, 'eventSourceARN': queue_arn(self.settings.source_queue_url),
                  'messageAttributes': {'qrDemoMode': {'stringValue': 'TIMEOUT_AFTER'}}}
        output = io.StringIO()
        with patch.dict(os.environ, {'QR_BUSINESS_TABLE': self.settings.business_table,
                                     'QR_REPLAY_QUEUE_ARN': queue_arn(self.settings.replay_queue_url),
                                     'AWS_DEFAULT_REGION': self.settings.region}), redirect_stdout(output):
            failed = consumer({'Records': [record]}, None)
            checked = verifier({'operationId': 'shipment:o1'}, None)
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(lines[0]['outcome'], 'TIMEOUT_AFTER')
        self.assertEqual(lines[1]['event'], 'VERIFICATION_CHECKED')
        self.assertEqual(checked['status'], 'COMPLETE')
        self.assertEqual(failed['batchItemFailures'], [{'itemIdentifier': 'source-id'}])
