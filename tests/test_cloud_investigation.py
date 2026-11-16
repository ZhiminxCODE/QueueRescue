import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from cloud_support import AWS_TESTS_AVAILABLE, CloudFixture

if AWS_TESTS_AVAILABLE:
    from cloud.api import route
    from cloud.aws import Repository, wire
    from cloud.collector import collect
    from cloud.contracts import CloudProblem
    from cloud.investigation import browse, incident_detail, incident_writes


@unittest.skipUnless(AWS_TESTS_AVAILABLE, 'Install requirements-aws-test.txt for offline AWS SDK tests')
class InvestigationTests(CloudFixture):
    def setUp(self):
        super().setUp()
        self.now = 1800000000
        self.repo.clock = lambda: self.now

    def rows(self, count, match_at=None):
        # Metadata only: deliberately omit S3 bodies from this read-path fixture.
        with _Batch(self.clients.ddb, self.repo.table) as batch:
            for i in range(count):
                batch.put({'pk': self.repo.queue_pk, 'sk': f'MSG#{i:05}', 'id': f'{i:05}',
                    'operationId': 'Shipment:Needle' if i == match_at else 'shipment:ordinary',
                    'originalMessageId': 'source-' + str(i), 'valid': i % 2 == 0,
                    'error': 'WarehouseTimeout', 'observedAt': self.now - count + i,
                    'expires_at': self.now + 1000})

    def test_incidents_group_schema_queue_version_and_utc_hour(self):
        self.put_dlq()
        self.put_dlq(body=self.body.replace('o1', 'o2'))
        self.put_dlq(body='invalid-json-with-private-data')
        collect(self.repo)
        groups = browse(self.repo, {}, incidents=True)['items']
        self.assertEqual(sorted(g['collectedCount'] for g in groups), [1, 2])
        valid = next(g for g in groups if g['category'] == 'UNCLASSIFIED')
        self.assertNotIn('timeout', valid['label'].lower())
        self.assertEqual(valid['windowStart'] % 3600, 0)
        self.assertEqual(valid['windowEnd'] - valid['windowStart'], 3600)
        self.now += 3600
        self.put_dlq(body=self.body.replace('o1', 'o3'))
        collect(self.repo)
        self.assertEqual(len(browse(self.repo, {}, incidents=True)['items']), 3)
        self.repo.settings = replace(self.repo.settings, consumer_version='new-consumer')
        self.put_dlq(body=self.body.replace('o1', 'o4'))
        collect(self.repo)
        self.assertEqual(len(browse(self.repo, {}, incidents=True)['items']), 4)

    def test_duplicate_collection_and_conditional_conflict_never_inflate_counts(self):
        self.put_dlq()
        collect(self.repo)
        collect(self.repo)
        item = self.repo.query(self.repo.queue_pk, 'MSG#')[0][0]
        with self.assertRaises(CloudProblem):
            self.repo.commit(incident_writes(self.repo, item), 'collector', 'DUPLICATE', item['id'])
        group = browse(self.repo, {}, incidents=True)['items'][0]
        self.assertEqual(group['collectedCount'], 1)
        self.assertEqual(len(browse(self.repo, {}, incident_id=group['id'])['items']), 1)

    def test_interrupted_index_commit_recovers_without_orphan_membership(self):
        self.put_dlq()
        with patch.object(self.repo, 'commit', side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError):
                collect(self.repo)
        self.assertEqual(browse(self.repo, {}, incidents=True)['items'], [])
        collect(self.repo)
        self.assertEqual(browse(self.repo, {}, incidents=True)['items'][0]['collectedCount'], 1)

    def test_sparse_search_continues_after_bounded_empty_page(self):
        self.rows(450, match_at=420)
        with patch.object(self.repo, 'query', wraps=self.repo.query) as query:
            first = browse(self.repo, {'q': 'needle'})
            self.assertEqual(query.call_count, 4)
        self.assertEqual(first['items'], [])
        self.assertEqual(first['evaluatedCount'], 400)
        self.assertTrue(first['readBudgetReached'])
        second = browse(self.repo, {'q': 'NEEDLE', 'cursor': first['nextCursor']})
        self.assertEqual([m['id'] for m in second['items']], ['00420'])
        self.assertIsNone(second['nextCursor'])

    def test_pagination_does_not_skip_matches_in_overfetched_query_pages(self):
        self.rows(105)
        cursor, identifiers = None, []
        for _ in range(30):
            page = browse(self.repo, {'schema': 'valid', 'limit': '3', **({'cursor': cursor} if cursor else {})})
            identifiers.extend(row['id'] for row in page['items'])
            cursor = page['nextCursor']
            if not cursor:
                break
        self.assertIsNone(cursor)
        self.assertEqual(identifiers, [f'{i:05}' for i in range(0, 105, 2)])

    def test_cursors_are_bound_to_filters_route_and_queue(self):
        self.rows(5)
        cursor = browse(self.repo, {'limit': '1'})['nextCursor']
        for q, incidents in [({'cursor': cursor, 'q': 'different'}, False), ({'cursor': cursor}, True)]:
            with self.assertRaises(CloudProblem) as failure:
                browse(self.repo, q, incidents=incidents)
            self.assertEqual(failure.exception.status, 400)
        other = Repository(replace(self.settings, dlq_url=self.settings.dlq_url + '-other'), self.clients, clock=self.repo.clock)
        with self.assertRaises(CloudProblem):
            browse(other, {'cursor': cursor})
        for bad in ('!', 'e30=', 'W10=', 'bnVsbA==', 'a' * 4097):
            with self.assertRaises(CloudProblem):
                browse(self.repo, {'cursor': bad})

    def test_member_access_requires_incident_in_current_queue(self):
        self.put_dlq()
        collect(self.repo)
        identifier = browse(self.repo, {}, incidents=True)['items'][0]['id']
        other = Repository(replace(self.settings, dlq_url=self.settings.dlq_url + '-other'), self.clients, clock=self.repo.clock)
        with self.assertRaises(CloudProblem) as failure:
            browse(other, {}, incident_id=identifier)
        self.assertEqual(failure.exception.status, 404)
        self.assertEqual(incident_detail(self.repo, identifier)['collectedCount'], 1)

    def test_time_category_and_schema_filters_exclude_expired_metadata(self):
        self.rows(6)
        page = browse(self.repo, {'category': 'INVALID_SCHEMA', 'observedFrom': str(self.now - 3),
                                 'observedTo': str(self.now - 1)})
        self.assertEqual([r['id'] for r in page['items']], ['00003', '00005'])
        self.now += 1001
        self.assertEqual(browse(self.repo, {})['items'], [])

    def test_expired_members_do_not_change_historical_collected_count(self):
        self.put_dlq()
        collect(self.repo)
        group = browse(self.repo, {}, incidents=True)['items'][0]
        self.now += self.settings.retention_days * 86400 + 1
        self.assertEqual(browse(self.repo, {}, incident_id=group['id'])['items'], [])
        self.assertEqual(incident_detail(self.repo, group['id'])['collectedCount'], 1)
        self.now = group['expires_at']
        self.assertEqual(browse(self.repo, {}, incidents=True)['items'], [])
        with self.assertRaises(CloudProblem) as failure:
            incident_detail(self.repo, group['id'])
        self.assertEqual(failure.exception.status, 404)

    def test_legacy_labels_are_unclassified_and_group_reads_allowlist_fields(self):
        self.rows(2)
        self.assertEqual(browse(self.repo, {})['items'][0]['error'], 'UnclassifiedFailure')
        self.put_dlq()
        collect(self.repo)
        group = browse(self.repo, {}, incidents=True)['items'][0]
        self.clients.ddb.update_item(TableName=self.repo.table,
            Key=wire({'pk': self.repo.queue_pk, 'sk': 'INC#' + group['id']}),
            UpdateExpression='SET privateEmail=:value', ExpressionAttributeValues=wire({':value': 'secret@example.test'}))
        self.assertNotIn('privateEmail', incident_detail(self.repo, group['id']))
        self.assertNotIn('privateEmail', browse(self.repo, {}, incidents=True)['items'][0])

    def test_unknown_filters_and_invalid_boundaries_fail_closed(self):
        for q in ({'q': 'a' * 129}, {'q': 'secret\nvalue'}, {'q': []}, {'limit': '0'}, {'limit': '101'},
                  {'category': 'TIMEOUT'}, {'schema': 'eligible'}, {'observedFrom': '-1'},
                  {'observedFrom': '2', 'observedTo': '1'}, {'observedFrom': 'NaN'}, {'typo': 'x'}):
            with self.subTest(q=q), self.assertRaises(CloudProblem) as failure:
                browse(self.repo, q)
            self.assertEqual(failure.exception.status, 400)

    def test_lists_never_fetch_bodies_invoke_consumers_or_expose_payload_fields(self):
        self.put_dlq(body=self.body[:-1] + ',"privateEmail":"secret@example.test"}')
        collect(self.repo)
        with patch.object(self.clients.s3, 'get_object', side_effect=AssertionError('S3 read')), \
             patch.object(self.clients.sqs, 'receive_message', side_effect=AssertionError('SQS receive')), \
             patch.object(self.verifier, 'invoke', side_effect=AssertionError('Lambda invoke')):
            page = browse(self.repo, {})
            group = browse(self.repo, {}, incidents=True)['items'][0]
            members = browse(self.repo, {}, incident_id=group['id'])
            self.assertEqual(browse(self.repo, {'q': 'secret@example.test'})['items'], [])
        self.assertNotIn('secret@example.test', json.dumps([page, group, members]))
        self.assertNotIn('evidenceKey', page['items'][0])

    def gateway(self, path, query=None, group='Viewer'):
        return {'rawPath': '/api' + path, 'queryStringParameters': query or {},
                'requestContext': {'http': {'method': 'GET'}, 'authorizer': {'jwt': {'claims': {
                    'sub': 'viewer-1', 'token_use': 'access', 'cognito:groups': [group]}}}}}

    def test_gateway_viewer_routes_and_missing_auth(self):
        self.put_dlq()
        collect(self.repo)
        group = route(self.repo, self.gateway('/incidents'))['items'][0]
        self.assertEqual(route(self.repo, self.gateway('/incidents/' + group['id']))['id'], group['id'])
        self.assertEqual(len(route(self.repo, self.gateway('/incidents/' + group['id'] + '/messages'))['items']), 1)
        self.assertEqual(len(route(self.repo, self.gateway('/messages', {'q': 'SHIPMENT:O1'}))['items']), 1)
        with self.assertRaises(CloudProblem) as failure:
            route(self.repo, {'rawPath': '/api/incidents', 'requestContext': {'http': {'method': 'GET'}}})
        self.assertEqual(failure.exception.status, 401)


class _Batch:
    def __init__(self, client, table):
        self.client, self.table, self.items = client, table, []

    def __enter__(self):
        return self

    def put(self, item):
        self.items.append({'PutRequest': {'Item': wire(item)}})
        if len(self.items) == 25:
            self.flush()

    def flush(self):
        if self.items:
            response = self.client.batch_write_item(RequestItems={self.table: self.items})
            if response.get('UnprocessedItems'):
                raise AssertionError('Offline fixture write was incomplete')
            self.items = []

    def __exit__(self, *args):
        self.flush()
