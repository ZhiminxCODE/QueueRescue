"""Offline AWS fixtures. Optional SDK dependencies never trigger real credentials."""
import io
import json
import unittest

try:
    import boto3
    from moto import mock_aws
    from cloud.aws import Clients, Repository, wire
    from cloud.contracts import Settings
    AWS_TESTS_AVAILABLE = True
except ImportError:
    AWS_TESTS_AVAILABLE = False


class FakeVerifier:
    def __init__(self):
        self.statuses = {}
        self.failure = None

    def invoke(self, **kwargs):
        if self.failure:
            raise self.failure
        operation = json.loads(kwargs['Payload'])['operationId']
        return {'Payload': io.BytesIO(json.dumps({'status': self.statuses.get(operation, 'UNKNOWN')}).encode())}


class CloudFixture(unittest.TestCase):
    def setUp(self):
        self.mock = mock_aws()
        self.mock.start()
        self.addCleanup(self.mock.stop)
        region = 'us-east-1'
        ddb = boto3.client('dynamodb', region_name=region)
        for name in ['qr-ledger', 'qr-business', 'qr-audit']:
            ddb.create_table(TableName=name, KeySchema=[{'AttributeName':'pk','KeyType':'HASH'},{'AttributeName':'sk','KeyType':'RANGE'}],
                AttributeDefinitions=[{'AttributeName':'pk','AttributeType':'S'},{'AttributeName':'sk','AttributeType':'S'}], BillingMode='PAY_PER_REQUEST')
        s3, sqs = boto3.client('s3', region_name=region), boto3.client('sqs', region_name=region)
        s3.create_bucket(Bucket='qr-evidence-test')
        key = boto3.client('kms', region_name=region).create_key()['KeyMetadata']['Arn']
        urls = [sqs.create_queue(QueueName=name)['QueueUrl'] for name in ['source', 'source-dlq', 'replay']]
        self.verifier = FakeVerifier()
        self.settings = Settings(region, '123456789012', 'qr-ledger', 'qr-business', 'qr-audit', 'qr-evidence-test', key,
            *urls, 'arn:aws:lambda:us-east-1:123456789012:function:verifier', 'arn:aws:states:us-east-1:123456789012:stateMachine:recovery')
        self.clients = Clients(ddb, s3, sqs, self.verifier, None)
        self.repo = Repository(self.settings, self.clients)
        self.body = json.dumps({'eventId':'e1','operationId':'shipment:o1','eventType':'SHIP_ORDER','schemaVersion':1,'orderId':'o1','warehouseId':'w1'}, indent=2)

    def business(self, status='NOT_STARTED'):
        item = {'pk':'OP#shipment:o1','sk':'META','status':status}
        if status == 'COMPLETE':
            item['shipmentId'] = 'existing-shipment'
        self.clients.ddb.put_item(TableName=self.settings.business_table, Item=wire(item))

    def put_dlq(self, body=None, attributes=None):
        return self.clients.sqs.send_message(QueueUrl=self.settings.dlq_url, MessageBody=body or self.body,
            MessageAttributes=attributes or {'correlationId':{'DataType':'String','StringValue':'correlation-1'}})
