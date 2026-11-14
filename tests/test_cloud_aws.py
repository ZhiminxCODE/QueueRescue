import json
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

from cloud_support import AWS_TESTS_AVAILABLE, CloudFixture

if AWS_TESTS_AVAILABLE:
    from botocore.exceptions import ReadTimeoutError
    from cloud.aws import Clients, new_item, wire
    from cloud.collector import collect
    from cloud.contracts import CloudProblem
    from cloud.shipment import consumer, process_record, verifier


@unittest.skipUnless(AWS_TESTS_AVAILABLE, 'Install requirements-aws-test.txt for offline AWS SDK tests')
class AwsAdapterTests(CloudFixture):
    def test_client_factory_disables_ambiguous_transport_retries(self):
        clients = Clients.create('us-east-1')
        self.assertEqual(clients.sqs.meta.config.retries['total_max_attempts'], 1)
        self.assertEqual(clients.verifier.meta.config.retries['total_max_attempts'], 1)

    def test_collector_preserves_bytes_and_never_deletes_originals(self):
        self.put_dlq()
        result = collect(self.repo)
        self.assertEqual(len(result['indexed']), 1)
        rows, _ = self.repo.query(self.repo.queue_pk, 'MSG#')
        snapshot = self.repo.evidence(rows[0]['evidenceKey'])
        self.assertEqual(snapshot['body'].encode(), self.body.encode())
        response = self.clients.sqs.receive_message(QueueUrl=self.settings.dlq_url)
        self.assertEqual(len(response['Messages']), 1)
        self.assertEqual(response['Messages'][0]['Body'], self.body)

    def test_duplicate_collection_indexes_only_one_snapshot(self):
        self.put_dlq()
        first = collect(self.repo)
        second = collect(self.repo)
        self.assertEqual(first['indexed'], second['indexed'])
        self.assertEqual(len(self.repo.query(self.repo.queue_pk, 'MSG#')[0]), 1)

    def test_collector_recovers_after_snapshot_commit_but_before_index_commit(self):
        self.put_dlq()
        with patch.object(self.repo, 'commit', side_effect=RuntimeError('Interrupted before index commit')):
            with self.assertRaises(RuntimeError):
                collect(self.repo)
        self.assertEqual(len(self.repo.query(self.repo.queue_pk, 'MSG#')[0]), 0)
        result = collect(self.repo)
        self.assertEqual(len(result['indexed']), 1)
        row = self.repo.query(self.repo.queue_pk, 'MSG#')[0][0]
        snapshot = self.repo.evidence(row['evidenceKey'], row['evidenceChecksum'])
        self.assertEqual(snapshot['body'], self.body)
        self.assertEqual(snapshot['systemAttributes']['ApproximateReceiveCount'], '1')

    def test_binary_attributes_survive_encrypted_snapshot(self):
        self.put_dlq(attributes={'binary':{'DataType':'Binary','BinaryValue':b'\x00\xff'}})
        collect(self.repo)
        row = self.repo.query(self.repo.queue_pk, 'MSG#')[0][0]
        self.assertEqual(self.repo.evidence(row['evidenceKey'])['messageAttributes']['binary']['BinaryValue'], b'\x00\xff')
        obj = self.clients.s3.head_object(Bucket=self.settings.evidence_bucket, Key=row['evidenceKey'])
        self.assertEqual(obj['ServerSideEncryption'], 'aws:kms')

    def test_minimal_profile_keeps_evidence_encrypted_with_service_owned_keys(self):
        self.repo.settings = replace(self.settings, kms_key_arn='')
        self.put_dlq()
        collect(self.repo)
        row = self.repo.query(self.repo.queue_pk, 'MSG#')[0][0]
        obj = self.clients.s3.head_object(Bucket=self.settings.evidence_bucket, Key=row['evidenceKey'])
        self.assertEqual(obj['ServerSideEncryption'], 'AES256')
        self.assertNotIn('SSEKMSKeyId', obj)
        self.assertEqual(self.repo.evidence(row['evidenceKey'], row['evidenceChecksum'])['body'], self.body)

    def test_snapshot_tampering_is_detected(self):
        self.put_dlq()
        collect(self.repo)
        row = self.repo.query(self.repo.queue_pk, 'MSG#')[0][0]
        self.clients.s3.put_object(Bucket=self.settings.evidence_bucket, Key=row['evidenceKey'], Body=b'changed')
        with self.assertRaises(CloudProblem):
            self.repo.evidence(row['evidenceKey'])

    def test_conditional_conflict_does_not_append_audit_or_partial_write(self):
        item = {'pk':'LOCK#operation','sk':'META','job':'job-1'}
        self.repo.commit([new_item(self.repo.table, item)], 'operator', 'RESERVE', 'job-1')
        with self.assertRaises(CloudProblem):
            self.repo.commit([new_item(self.repo.table, item), new_item(self.repo.table, {'pk':'JOB#2','sk':'META'})], 'operator', 'RESERVE', 'job-2')
        self.assertIsNone(self.repo.get('JOB#2'))
        self.assertEqual(len(self.repo.query('AUDIT', '', table=self.settings.audit_table)[0]), 1)

    def test_status_verifier_absence_and_timeouts_are_unknown(self):
        self.assertEqual(self.repo.business_outcome('shipment:o1'), 'UNKNOWN')
        self.verifier.failure = ReadTimeoutError(endpoint_url='https://lambda.us-east-1.amazonaws.com')
        self.assertEqual(self.repo.business_outcome('shipment:o1'), 'UNKNOWN')

    def record(self, replay=True, mode='TIMEOUT_BEFORE'):
        arn = 'arn:aws:sqs:us-east-1:123456789012:replay'
        return {'messageId':'message-1','body':self.body,'eventSourceARN':arn if replay else 'arn:aws:sqs:us-east-1:123456789012:source',
                'messageAttributes':{'qrDemoMode':{'stringValue':mode}}}, arn

    def test_shipment_consumer_deduplicates_real_conditional_writes(self):
        self.business()
        record, arn = self.record()
        process_record(self.clients.ddb, self.settings.business_table, record, arn)
        first = self.repo.get('OP#shipment:o1', table=self.settings.business_table)
        process_record(self.clients.ddb, self.settings.business_table, record, arn)
        second = self.repo.get('OP#shipment:o1', table=self.settings.business_table)
        self.assertEqual(first['shipmentId'], second['shipmentId'])
        self.assertEqual(second['status'], 'COMPLETE')

    def test_source_after_effect_fault_persists_until_dlq(self):
        self.business()
        record, arn = self.record(replay=False, mode='TIMEOUT_AFTER')
        for _ in range(2):
            with self.assertRaises(TimeoutError):
                process_record(self.clients.ddb, self.settings.business_table, record, arn)
        self.assertEqual(self.repo.get('OP#shipment:o1', table=self.settings.business_table)['status'], 'COMPLETE')

    def test_source_fault_cannot_be_bypassed_with_replay_attributes(self):
        self.business()
        record, arn = self.record(replay=False)
        record['messageAttributes']['replayJobId'] = {'stringValue':'spoofed'}
        with self.assertRaises(TimeoutError):
            process_record(self.clients.ddb, self.settings.business_table, record, arn)
        self.assertEqual(self.repo.get('OP#shipment:o1', table=self.settings.business_table)['status'], 'NOT_STARTED')

    def test_partial_batch_response_only_returns_failed_messages(self):
        self.business()
        good, arn = self.record()
        bad = {**good, 'messageId':'bad', 'body':'not json'}
        with patch.dict(os.environ, {'QR_BUSINESS_TABLE':self.settings.business_table,'QR_REPLAY_QUEUE_ARN':arn,'AWS_DEFAULT_REGION':'us-east-1'}):
            self.assertEqual(consumer({'Records':[good,bad]}, None), {'batchItemFailures':[{'itemIdentifier':'bad'}]})

    def test_business_verifier_never_invents_proof_for_missing_records(self):
        with patch.dict(os.environ, {'QR_BUSINESS_TABLE':self.settings.business_table,'AWS_DEFAULT_REGION':'us-east-1'}):
            self.assertEqual(verifier({'operationId':'shipment:o1'}, None)['status'], 'UNKNOWN')
            self.business('COMPLETE')
            self.assertEqual(verifier({'operationId':'shipment:o1'}, None)['status'], 'COMPLETE')
