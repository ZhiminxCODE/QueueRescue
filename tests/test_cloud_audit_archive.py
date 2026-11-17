import base64
import copy
import hashlib
import json
import unittest
import contextlib
import io
from unittest.mock import patch
from cloud_support import AWS_TESTS_AVAILABLE

if AWS_TESTS_AVAILABLE:
    import boto3
    from botocore.stub import Stubber
    from botocore.exceptions import ReadTimeoutError
    from cloud.audit_archive import archive_batch, handler

STREAM = 'arn:aws:dynamodb:us-east-1:123456789012:table/audit/stream/2026-01-01T00:00:00.000'
BUCKET = 'synthetic-audit-archive'


@unittest.skipUnless(AWS_TESTS_AVAILABLE, 'Install requirements-aws-test.txt for offline SDK tests')
class AuditArchiveTests(unittest.TestCase):
    def setUp(self):
        self.s3 = boto3.client('s3', region_name='us-east-1', aws_access_key_id='testing', aws_secret_access_key='testing')
        self.stub = Stubber(self.s3)
        self.stub.activate()
        self.addCleanup(self.stub.deactivate)
        self.record = {'eventID':'event-1', 'eventName':'INSERT', 'eventSource':'aws:dynamodb', 'eventSourceARN':STREAM,
            'dynamodb':{'SequenceNumber':'100', 'Keys':{'pk':{'S':'AUDIT'}, 'sk':{'S':'123#event'}},
                        'NewImage':{'pk':{'S':'AUDIT'}, 'sk':{'S':'123#event'}, 'actor':{'S':'synthetic-actor'}}}}

    def put_args(self, record):
        body = json.dumps(record,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
        digest = hashlib.sha256(body).digest()
        return {'Bucket':BUCKET, 'Key':'audit-records/'+hashlib.sha256(STREAM.encode()).hexdigest()+'/'+hashlib.sha256((record['eventID']+'\n'+digest.hex()).encode()).hexdigest()+'.json',
                'Body':body, 'ContentType':'application/json', 'ServerSideEncryption':'AES256', 'IfNoneMatch':'*',
                'ChecksumAlgorithm':'SHA256', 'ChecksumSHA256':base64.b64encode(digest).decode(), 'Metadata':{'sha256':digest.hex()}}

    def test_writes_full_record_with_checksum_and_conditional_creation(self):
        expected = self.put_args(self.record)
        self.stub.add_response('put_object', {'VersionId':'version-1'}, expected)
        self.assertEqual(archive_batch(self.s3, {'Records':[self.record]}, BUCKET, STREAM), {'batchItemFailures':[]})
        self.stub.assert_no_pending_responses()

    def test_accepted_then_retried_write_checks_existing_content(self):
        args=self.put_args(self.record)
        self.stub.add_client_error('put_object','PreconditionFailed',http_status_code=412,expected_params=args)
        self.stub.add_response('head_object',{'ChecksumSHA256':args['ChecksumSHA256']},
                               {'Bucket':BUCKET,'Key':args['Key'],'ChecksumMode':'ENABLED'})
        self.assertEqual(archive_batch(self.s3,{'Records':[self.record]},BUCKET,STREAM),{'batchItemFailures':[]})
        self.stub.assert_no_pending_responses()

    def test_wrong_existing_checksum_remains_failed(self):
        args=self.put_args(self.record)
        self.stub.add_client_error('put_object','PreconditionFailed',http_status_code=412,expected_params=args)
        self.stub.add_response('head_object',{'ChecksumSHA256':'different'},{'Bucket':BUCKET,'Key':args['Key'],'ChecksumMode':'ENABLED'})
        self.assertEqual(archive_batch(self.s3,{'Records':[self.record]},BUCKET,STREAM),{'batchItemFailures':[{'itemIdentifier':'100'}]})

    def test_access_denial_or_uncertain_put_stops_at_first_failure(self):
        later=copy.deepcopy(self.record);later['eventID']='event-2';later['dynamodb']['SequenceNumber']='101'
        for code in ('AccessDenied','InternalError','ConditionalRequestConflict'):
            self.stub.add_client_error('put_object',code,http_status_code=403 if code=='AccessDenied' else 500,expected_params=self.put_args(self.record))
            self.assertEqual(archive_batch(self.s3,{'Records':[self.record,later]},BUCKET,STREAM),{'batchItemFailures':[{'itemIdentifier':'100'}]})
        self.stub.assert_no_pending_responses()

    def test_transport_timeout_never_acknowledges_a_record_as_archived(self):
        with patch.object(self.s3, 'put_object', side_effect=ReadTimeoutError(endpoint_url='https://s3.us-east-1.amazonaws.com')) as put:
            self.assertEqual(archive_batch(self.s3,{'Records':[self.record]},BUCKET,STREAM),{'batchItemFailures':[{'itemIdentifier':'100'}]})
            self.assertEqual(put.call_count,1)

    def test_updates_and_deletions_preserve_old_images(self):
        for name in ('MODIFY','REMOVE'):
            record=copy.deepcopy(self.record);record['eventName']=name
            record['dynamodb']['OldImage']=copy.deepcopy(record['dynamodb']['NewImage'])
            if name=='REMOVE': del record['dynamodb']['NewImage']
            self.stub.add_response('put_object',{},self.put_args(record))
            self.assertEqual(archive_batch(self.s3,{'Records':[record]},BUCKET,STREAM),{'batchItemFailures':[]})

    def test_untrusted_source_and_malformed_records_never_write(self):
        guard = patch.object(self.s3, 'put_object', side_effect=AssertionError('invalid record must never write'))
        guard.start()
        self.addCleanup(guard.stop)
        for field,value in [('eventSourceARN',STREAM+'-other'),('eventSource','aws:sqs'),('eventName','OTHER')]:
            record={**self.record,field:value}
            self.assertEqual(archive_batch(self.s3,{'Records':[record]},BUCKET,STREAM),{'batchItemFailures':[{'itemIdentifier':'100'}]})
        for change in ('wrong-key','missing-image'):
            record=copy.deepcopy(self.record)
            if change=='wrong-key': record['dynamodb']['Keys']['pk']['S']='BUSINESS'
            else: del record['dynamodb']['NewImage']
            self.assertEqual(archive_batch(self.s3,{'Records':[record]},BUCKET,STREAM),{'batchItemFailures':[{'itemIdentifier':'100'}]})
        record=copy.deepcopy(self.record);del record['dynamodb']['SequenceNumber']
        with self.assertRaises(ValueError): archive_batch(self.s3,{'Records':[record]},BUCKET,STREAM)
        with self.assertRaises(ValueError): archive_batch(self.s3,{'Records':[self.record]*101},BUCKET,STREAM)
        self.assertEqual(archive_batch(self.s3,{'Records':[]},BUCKET,STREAM),{'batchItemFailures':[]})

    def test_runtime_uses_only_bound_archive_settings_and_one_transport_attempt(self):
        with patch.dict('os.environ',{'QR_AUDIT_ARCHIVE_BUCKET':BUCKET,'QR_AUDIT_STREAM_ARN':STREAM,'QR_REGION':'us-east-1'},clear=True), patch('cloud.audit_archive.boto3.client',return_value=self.s3) as client:
            self.assertEqual(handler({'Records':[]},None),{'batchItemFailures':[]})
            self.assertEqual(client.call_args.args,('s3',))
            self.assertEqual(client.call_args.kwargs['config'].retries['total_max_attempts'],1)

    def test_partial_failure_metric_marker_contains_no_record_data(self):
        output = io.StringIO()
        args = self.put_args(self.record)
        self.stub.add_client_error('put_object','AccessDenied',http_status_code=403,expected_params=args)
        with patch.dict('os.environ',{'QR_AUDIT_ARCHIVE_BUCKET':BUCKET,'QR_AUDIT_STREAM_ARN':STREAM,'QR_REGION':'us-east-1'},clear=True), patch('cloud.audit_archive.boto3.client',return_value=self.s3), contextlib.redirect_stdout(output):
            result = handler({'Records':[self.record]},None)
        self.assertEqual(result,{'batchItemFailures':[{'itemIdentifier':'100'}]})
        self.assertEqual(output.getvalue(),'{"event":"AUDIT_ARCHIVE_FAILED"}\n')

    def test_invalid_runtime_stream_never_constructs_a_client(self):
        with patch.dict('os.environ',{'QR_AUDIT_ARCHIVE_BUCKET':BUCKET,'QR_AUDIT_STREAM_ARN':'invalid','QR_REGION':'us-east-1'},clear=True), patch('cloud.audit_archive.boto3.client') as client:
            with self.assertRaises(ValueError): handler({'Records':[]},None)
            client.assert_not_called()

if __name__=='__main__': unittest.main()
