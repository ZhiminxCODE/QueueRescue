import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from cloud_support import AWS_TESTS_AVAILABLE, CloudFixture

if AWS_TESTS_AVAILABLE:
    from botocore.exceptions import ClientError, ReadTimeoutError
    from botocore.stub import Stubber
    from cloud.api import route
    from cloud.aws import wire
    from cloud.collector import collect
    from cloud.contracts import CloudProblem
    from cloud.investigation import browse
    from cloud.recovery import classify
    from cloud.retention import describe, queue_retention, sent_seconds
    from cloud.telemetry import queue_arn


@unittest.skipUnless(AWS_TESTS_AVAILABLE, 'Install requirements-aws-test.txt for offline AWS SDK tests')
class RetentionCalculationTests(unittest.TestCase):
    def setUp(self):
        self.now = 1800000000
        self.observation = {'status': 'OBSERVED', 'periodSeconds': 4 * 86400, 'checkedAt': self.now}

    def test_standard_dlq_expiry_uses_original_enqueue_not_collection(self):
        row = {'sourceSentAt': self.now - 3 * 86400 - 3600, 'observedAt': self.now,
               'expires_at': self.now + 7 * 86400}
        result = describe(row, self.observation, self.now)
        self.assertEqual(result['original']['remainingSeconds'], 23 * 3600)
        self.assertEqual(result['original']['enqueueAgeSeconds'], 3 * 86400 + 3600)
        self.assertEqual(result['original']['status'], 'NEAR_EXPIRY')
        self.assertEqual(result['snapshot']['remainingSeconds'], 7 * 86400)
        self.assertEqual(result['snapshot']['status'], 'WITHIN_ACCESS_WINDOW')
        self.assertEqual(result['original']['presence'], 'UNVERIFIED')

    def test_warning_and_expiry_boundaries_are_independent(self):
        row = {'sourceSentAt': self.now - 3 * 86400, 'expires_at': self.now + 86400}
        result = describe(row, self.observation, self.now)
        self.assertEqual(result['original']['status'], 'NEAR_EXPIRY')
        self.assertEqual(result['snapshot']['status'], 'NEAR_EXPIRY')
        row['sourceSentAt'] -= 1
        row['expires_at'] += 1
        result = describe(row, self.observation, self.now)
        self.assertEqual(result['snapshot']['status'], 'WITHIN_ACCESS_WINDOW')
        result = describe(row, self.observation, self.now + 86401)
        self.assertEqual(result['original']['status'], 'EXPIRY_ESTIMATE_PASSED')
        self.assertEqual(result['snapshot']['status'], 'EXPIRED')
        self.assertEqual(result['original']['remainingSeconds'], 0)

    def test_unknown_denied_and_bad_timestamps_never_invent_a_deadline(self):
        for sent in (None, 0, -1, True, '1800000000', self.now + 1, 253402300800):
            with self.subTest(sent=sent):
                result = describe({'sourceSentAt': sent, 'expires_at': self.now + 86400}, self.observation, self.now)
                self.assertEqual(result['original']['status'], 'UNKNOWN')
                self.assertIsNone(result['original']['enqueueAgeSeconds'])
                self.assertIsNone(result['original']['estimatedExpiresAt'])
        row = {'sourceSentAt': self.now - 3600, 'expires_at': self.now + 60}
        result = describe(row, {'status': 'DENIED', 'checkedAt': self.now, 'periodSeconds': None}, self.now)
        self.assertEqual(result['original']['enqueueAgeSeconds'], 3600)
        self.assertIsNone(result['original']['estimatedExpiresAt'])
        self.assertEqual(result['snapshot']['status'], 'NEAR_EXPIRY')
        self.assertEqual(describe({}, self.observation, self.now)['snapshot']['status'], 'UNKNOWN')
        impossible = {'sourceSentAt': self.now - 10, 'observedAt': self.now - 20}
        self.assertEqual(describe(impossible, self.observation, self.now)['original']['status'], 'UNKNOWN')

    def test_sent_timestamp_parsing_is_conservative(self):
        self.assertEqual(sent_seconds('1800000000999'), 1800000000)
        for value in (None, '', 'bad', '-1', '1.5', [], True, '9' * 100, '0'):
            with self.subTest(value=value):
                self.assertEqual(sent_seconds(value), 0)


@unittest.skipUnless(AWS_TESTS_AVAILABLE, 'Install requirements-aws-test.txt for offline AWS SDK tests')
class RetentionAccessTests(CloudFixture):
    def setUp(self):
        super().setUp()
        self.now = 1800000000
        self.repo.clock = lambda: self.now
        self.put_dlq(body=self.body[:-1] + ',"customerEmail":"private@example.test"}')
        with redirect_stdout(io.StringIO()):
            self.identifier = collect(self.repo)['indexed'][0]
        self.row = self.repo.get(self.repo.queue_pk, 'MSG#' + self.identifier)

    def update_row(self, **fields):
        for pk in (self.repo.queue_pk, 'INCIDENT#' + self.row['incidentId']):
            row = {**self.row, **fields, 'pk': pk}
            self.clients.ddb.put_item(TableName=self.repo.table, Item=wire(row))
        self.row.update(fields)

    def gateway(self, path=None, group='Viewer'):
        return {'rawPath': '/api' + (path or '/messages/' + self.identifier),
                'requestContext': {'http': {'method': 'GET'}, 'authorizer': {'jwt': {'claims': {
                    'sub': 'reader-1', 'token_use': 'access', 'cognito:groups': [group]}}}}}

    def test_sdk_request_is_scoped_and_only_reads_required_dlq_attributes(self):
        with Stubber(self.clients.sqs) as stub:
            stub.add_response('get_queue_attributes', {'Attributes': {
                'QueueArn': queue_arn(self.settings.dlq_url), 'MessageRetentionPeriod': '1209600'}},
                {'QueueUrl': self.settings.dlq_url, 'AttributeNames': ['MessageRetentionPeriod', 'QueueArn']})
            result = queue_retention(self.repo)
            stub.assert_no_pending_responses()
        self.assertEqual(result, {'status': 'OBSERVED', 'periodSeconds': 1209600, 'checkedAt': self.now})

    def test_denied_missing_unavailable_and_invalid_metadata_are_unknown_without_error_leaks(self):
        for code, status in [('AccessDenied', 'DENIED'), ('AWS.SimpleQueueService.NonExistentQueue', 'MISSING_QUEUE'),
                             ('RequestThrottled', 'UNAVAILABLE')]:
            error = ClientError({'Error': {'Code': code, 'Message': 'private-token'}}, 'GetQueueAttributes')
            with self.subTest(code=code), patch.object(self.clients.sqs, 'get_queue_attributes', side_effect=error) as get:
                result = queue_retention(self.repo)
                self.assertEqual(get.call_count, 1)
            self.assertEqual(result['status'], status)
            self.assertIsNone(result['periodSeconds'])
            self.assertNotIn('private-token', json.dumps(result))
        with patch.object(self.clients.sqs, 'get_queue_attributes', side_effect=ReadTimeoutError(endpoint_url='private')):
            self.assertEqual(queue_retention(self.repo)['status'], 'UNAVAILABLE')
        for attrs in ({}, {'QueueArn': 'other', 'MessageRetentionPeriod': '86400'},
                      {'QueueArn': queue_arn(self.settings.dlq_url), 'MessageRetentionPeriod': '59'},
                      {'QueueArn': queue_arn(self.settings.dlq_url), 'MessageRetentionPeriod': '1209601'},
                      {'QueueArn': queue_arn(self.settings.dlq_url), 'MessageRetentionPeriod': 'private-value'}):
            with self.subTest(attrs=attrs), patch.object(self.clients.sqs, 'get_queue_attributes', return_value={'Attributes': attrs}):
                self.assertEqual(queue_retention(self.repo)['status'], 'INVALID_METADATA')

    def test_listing_reads_settings_once_and_never_reads_payloads_or_mutates_sqs(self):
        self.update_row(sourceSentAt=self.now - 4 * 86400 + 120)
        with patch.object(self.clients.sqs, 'get_queue_attributes', wraps=self.clients.sqs.get_queue_attributes) as get, \
             patch.object(self.clients.sqs, 'receive_message', side_effect=AssertionError('Receive is not a read-only inspection')), \
             patch.object(self.clients.sqs, 'send_message', side_effect=AssertionError('Send')), \
             patch.object(self.clients.sqs, 'delete_message', side_effect=AssertionError('Delete')), \
             patch.object(self.clients.sqs, 'change_message_visibility', side_effect=AssertionError('Visibility')), \
             patch.object(self.clients.s3, 'get_object', side_effect=AssertionError('Payload read')):
            page = route(self.repo, self.gateway('/messages'))
            self.assertEqual(get.call_count, 1)
            item = page['items'][0]
            self.assertEqual(item['retention']['original']['remainingSeconds'], 120)
            self.assertEqual(item['retention']['original']['status'], 'NEAR_EXPIRY')
            self.assertEqual(item['retention']['snapshot']['availability'], 'NOT_CHECKED')
            self.assertNotIn('private@example.test', json.dumps(page))
            members = browse(self.repo, {}, incident_id=self.row['incidentId'])
            self.assertEqual(members['items'][0]['retention'], item['retention'])

    def test_each_read_observes_retention_changes_and_failures_do_not_hide_snapshots(self):
        self.update_row(sourceSentAt=self.now - 86400)
        first = browse(self.repo, {})['items'][0]['retention']
        self.clients.sqs.set_queue_attributes(QueueUrl=self.settings.dlq_url, Attributes={'MessageRetentionPeriod': '86400'})
        second = browse(self.repo, {})['items'][0]['retention']
        self.assertEqual(first['original']['status'], 'ESTIMATED_WINDOW_OPEN')
        self.assertEqual(second['original']['status'], 'EXPIRY_ESTIMATE_PASSED')
        failure = ClientError({'Error': {'Code': 'AccessDenied'}}, 'GetQueueAttributes')
        with patch.object(self.clients.sqs, 'get_queue_attributes', side_effect=failure):
            third = browse(self.repo, {})['items'][0]['retention']
        self.assertEqual(third['original']['status'], 'UNKNOWN')
        self.assertEqual(third['snapshot'], first['snapshot'])

    def test_expired_or_unknown_snapshot_access_is_blocked_before_s3_or_sqs(self):
        with patch.object(self.clients.s3, 'get_object', side_effect=AssertionError('Expired payload access')), \
             patch.object(self.clients.sqs, 'get_queue_attributes', side_effect=AssertionError('Unnecessary request')):
            for deadline, status in ((self.now, 410), (self.now - 1, 410), (None, 409)):
                self.update_row(expires_at=deadline)
                with self.subTest(deadline=deadline), self.assertRaises(CloudProblem) as failure:
                    route(self.repo, self.gateway())
                self.assertEqual(failure.exception.status, status)

    def test_viewer_keeps_masked_snapshot_access_after_original_is_removed(self):
        received = self.clients.sqs.receive_message(QueueUrl=self.settings.dlq_url)['Messages'][0]
        self.clients.sqs.delete_message(QueueUrl=self.settings.dlq_url, ReceiptHandle=received['ReceiptHandle'])
        self.update_row(sourceSentAt=self.now - 4 * 86400 - 1)
        result = route(self.repo, self.gateway())
        self.assertEqual(result['payload']['customerEmail'], '[REDACTED]')
        self.assertEqual(result['originalPresence'], 'UNVERIFIED')
        self.assertFalse(result['sourceDeletionPerformed'])
        self.assertNotIn('originalsRetained', result)
        self.assertEqual(result['retention']['original']['status'], 'EXPIRY_ESTIMATE_PASSED')
        self.assertEqual(result['retention']['snapshot']['availability'], 'READ_SUCCEEDED')
        self.verifier.statuses['shipment:o1'] = 'NOT_STARTED'
        self.assertEqual(classify(self.repo, self.row)[0], 'ELIGIBLE')

    def test_snapshot_expiring_during_storage_read_is_not_returned(self):
        self.update_row(expires_at=self.now + 1)
        evidence = self.repo.evidence
        def slow_read(*args):
            result = evidence(*args)
            self.now += 2
            return result
        with patch.object(self.repo, 'evidence', side_effect=slow_read), \
             patch.object(self.clients.sqs, 'get_queue_attributes', side_effect=AssertionError('Expired inspection')):
            with self.assertRaises(CloudProblem) as failure:
                route(self.repo, self.gateway())
        self.assertEqual(failure.exception.status, 410)

    def test_storage_permissions_and_lifecycle_loss_never_return_a_payload(self):
        for code, expected in (('AccessDenied', 503), ('NoSuchKey', 410)):
            with self.subTest(code=code), patch.object(self.clients.s3, 'get_object', side_effect=ClientError(
                    {'Error': {'Code': code, 'Message': 'private-snapshot'}}, 'GetObject')):
                with self.assertRaises(CloudProblem) as failure:
                    route(self.repo, self.gateway())
            self.assertEqual(failure.exception.status, expected)
            self.assertNotIn('private-snapshot', failure.exception.message)
        self.clients.s3.put_bucket_versioning(Bucket=self.settings.evidence_bucket, VersioningConfiguration={'Status': 'Enabled'})
        self.clients.s3.delete_object(Bucket=self.settings.evidence_bucket, Key=self.row['evidenceKey'])
        versions = self.clients.s3.list_object_versions(Bucket=self.settings.evidence_bucket, Prefix=self.row['evidenceKey'])
        self.assertTrue(versions.get('Versions'))
        self.assertTrue(versions.get('DeleteMarkers'))
        with self.assertRaises(CloudProblem) as failure:
            route(self.repo, self.gateway())
        self.assertEqual(failure.exception.status, 410)

    def test_unauthenticated_and_other_queue_requests_cannot_inspect_evidence(self):
        with patch.object(self.clients.s3, 'get_object', side_effect=AssertionError('Unauthorized payload read')), \
             patch.object(self.clients.sqs, 'get_queue_attributes', side_effect=AssertionError('Unauthorized settings read')):
            event = self.gateway()
            event['requestContext'].pop('authorizer')
            with self.assertRaises(CloudProblem) as failure:
                route(self.repo, event)
            self.assertEqual(failure.exception.status, 401)
            self.repo.queue_pk = 'QUEUE#other'
            with self.assertRaises(CloudProblem) as failure:
                route(self.repo, self.gateway())
            self.assertEqual(failure.exception.status, 404)
