"""Archive exact audit stream records; never mutate recovery or business state."""
import base64
import hashlib
import json
import os
import re

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


def archive_batch(s3, event, bucket, stream_arn):
    records = event.get('Records')
    if not isinstance(records, list) or len(records) > 100:
        raise ValueError('Invalid audit stream batch')
    for record in records:
        sequence = record.get('dynamodb', {}).get('SequenceNumber') if isinstance(record, dict) else None
        if not isinstance(sequence, str) or not re.fullmatch(r'[0-9]{1,128}', sequence):
            # An invalid checkpoint cannot be acknowledged as a partial success.
            raise ValueError('Invalid audit stream checkpoint')
        try:
            change = record['dynamodb']
            kind = record.get('eventName')
            if (record.get('eventSourceARN') != stream_arn or record.get('eventSource') != 'aws:dynamodb'
                    or kind not in {'INSERT', 'MODIFY', 'REMOVE'}
                    or not isinstance(record.get('eventID'), str) or not 1 <= len(record['eventID']) <= 512
                    or change['Keys']['pk'] != {'S': 'AUDIT'} or not change['Keys']['sk'].get('S')):
                raise ValueError('Untrusted audit stream record')
            for image in (['NewImage'] if kind == 'INSERT' else ['OldImage'] if kind == 'REMOVE' else ['NewImage', 'OldImage']):
                if not isinstance(change.get(image), dict) or any(change[image].get(key) != value for key, value in change['Keys'].items()):
                    raise ValueError('Incomplete audit stream image')
            body = json.dumps(record, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
            digest = hashlib.sha256(body).digest()
            checksum = base64.b64encode(digest).decode()
            key = ('audit-records/' + hashlib.sha256(stream_arn.encode()).hexdigest() + '/' +
                   hashlib.sha256((record['eventID'] + '\n' + digest.hex()).encode()).hexdigest() + '.json')
            try:
                s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType='application/json',
                              ServerSideEncryption='AES256', IfNoneMatch='*', ChecksumAlgorithm='SHA256',
                              ChecksumSHA256=checksum, Metadata={'sha256': digest.hex()})
            except ClientError as error:
                if error.response['Error']['Code'] not in {'PreconditionFailed', '412'}:
                    raise
                # Verify the S3-computed checksum, not caller-supplied metadata.
                existing = s3.head_object(Bucket=bucket, Key=key, ChecksumMode='ENABLED')
                if existing.get('ChecksumSHA256') != checksum:
                    raise ValueError('Existing audit archive integrity mismatch') from None
        except (BotoCoreError, ClientError, ValueError, KeyError, TypeError, AttributeError):
            # No payloads, credentials or actor data are logged on failure. The
            # mapping retries from this checkpoint, then retains failed batches.
            return {'batchItemFailures': [{'itemIdentifier': sequence}]}
    return {'batchItemFailures': []}


def handler(event, context):
    region, bucket, stream = (os.environ[name] for name in ('QR_REGION', 'QR_AUDIT_ARCHIVE_BUCKET', 'QR_AUDIT_STREAM_ARN'))
    if not re.fullmatch(r'arn:aws:dynamodb:' + re.escape(region) + r':[0-9]{12}:table/[A-Za-z0-9_.-]+/stream/[0-9T:.\-]+', stream):
        raise ValueError('Invalid configured audit stream')
    client = boto3.client('s3', config=Config(region_name=region, connect_timeout=3, read_timeout=8,
                                           retries={'mode': 'standard', 'total_max_attempts': 1}))
    result = archive_batch(client, event, bucket, stream)
    if result['batchItemFailures']:
        print('{"event":"AUDIT_ARCHIVE_FAILED"}')
    return result
