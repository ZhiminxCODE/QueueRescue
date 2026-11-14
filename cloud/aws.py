"""Bounded AWS adapters. Construct clients only when a handler is invoked."""
import base64
from dataclasses import dataclass
from decimal import Decimal
import json
import time
import uuid

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from cloud.contracts import CloudProblem, OUTCOMES, encode, sha


SERIALIZER, DESERIALIZER = TypeSerializer(), TypeDeserializer()


def wire(item):
    return {key: SERIALIZER.serialize(value) for key, value in item.items()}


def plain(value):
    if isinstance(value, Decimal):
        return int(value) if value == int(value) else float(value)
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [plain(item) for item in value]
    return value


def unwire(item):
    return plain({key: DESERIALIZER.deserialize(value) for key, value in item.items()})


def new_item(table, item):
    return {"Put": {"TableName": table, "Item": wire(item), "ConditionExpression": "attribute_not_exists(pk)"}}


def condition(table, key, expression, values, names=None):
    action = {"TableName": table, "Key": wire(key), "ConditionExpression": expression,
              "ExpressionAttributeValues": wire(values)}
    if names:
        action["ExpressionAttributeNames"] = names
    return {"ConditionCheck": action}


def update(table, key, expression, expected, values, names=None):
    action = {"TableName": table, "Key": wire(key), "UpdateExpression": expression,
              "ConditionExpression": expected, "ExpressionAttributeValues": wire(values)}
    if names:
        action["ExpressionAttributeNames"] = names
    return {"Update": action}


@dataclass
class Clients:
    ddb: object
    s3: object
    sqs: object
    verifier: object
    states: object

    @classmethod
    def create(cls, region):
        # Never automatically repeat a possibly accepted SendMessage or Invoke.
        config = Config(region_name=region, connect_timeout=3, read_timeout=8,
                        retries={"mode": "standard", "total_max_attempts": 1})
        return cls(*(boto3.client(name, config=config) for name in ("dynamodb", "s3", "sqs", "lambda", "stepfunctions")))


class Repository:
    def __init__(self, settings, clients, clock=time.time):
        self.settings, self.clients, self.clock = settings, clients, clock
        self.table = settings.ledger_table
        self.queue_pk = "QUEUE#" + sha(settings.dlq_url)

    def get(self, pk, sk="META", table=None):
        result = self.clients.ddb.get_item(TableName=table or self.table, Key=wire({"pk": pk, "sk": sk}), ConsistentRead=True)
        return unwire(result["Item"]) if "Item" in result else None

    def query(self, pk, prefix, limit=25, cursor=None, table=None, descending=False):
        if not 1 <= limit <= 100 or (cursor and (cursor.get("pk") != pk or not cursor.get("sk", "").startswith(prefix))):
            raise CloudProblem(400, "Invalid page boundary")
        args = {"TableName": table or self.table, "KeyConditionExpression": "pk=:pk" + (" AND begins_with(sk,:prefix)" if prefix else ""),
                "ExpressionAttributeValues": wire({":pk": pk, **({":prefix": prefix} if prefix else {})}), "Limit": limit,
                "ConsistentRead": True, "ScanIndexForward": not descending}
        if cursor:
            args["ExclusiveStartKey"] = wire(cursor)
        result = self.clients.ddb.query(**args)
        return [unwire(item) for item in result.get("Items", [])], unwire(result["LastEvaluatedKey"]) if result.get("LastEvaluatedKey") else None

    def cached(self, request_key, fingerprint):
        row = self.get(request_key)
        if row and row["fingerprint"] != fingerprint:
            raise CloudProblem(409, "Idempotency-Key was used for a different request")
        return row.get("response") if row else None

    def commit(self, actions, actor, action, resource, detail=None, request=None, response=None):
        now = int(self.clock())
        audit = {"pk": "AUDIT", "sk": str(now).zfill(12) + "#" + uuid.uuid4().hex,
                 "at": now, "actor": actor, "action": action, "resource": resource,
                 "detail": detail or {}}
        writes = list(actions) + [new_item(self.settings.audit_table, audit)]
        if request:
            key, fingerprint = request
            writes.append(new_item(self.table, {"pk": key, "sk": "META", "fingerprint": fingerprint,
                                               "response": response, "expires_at": now + 7 * 86400}))
        if len(writes) > 100:
            raise CloudProblem(400, "This sandbox plan exceeds the transactional write limit")
        try:
            self.clients.ddb.transact_write_items(TransactItems=writes)
        except ClientError as error:
            if error.response["Error"]["Code"] in {"TransactionCanceledException", "ConditionalCheckFailedException"}:
                raise CloudProblem(409, "Concurrent state change or operation reservation conflict") from error
            raise

    def business_outcome(self, operation_id):
        try:
            response = self.clients.verifier.invoke(FunctionName=self.settings.verifier_arn,
                InvocationType="RequestResponse", Payload=encode({"operationId": operation_id}).encode())
            if response.get("FunctionError"):
                return "UNKNOWN"
            result = json.loads(response["Payload"].read())
            status = result.get("status") if isinstance(result, dict) else None
            return status if isinstance(status, str) and status in OUTCOMES else "UNKNOWN"
        except (BotoCoreError, ClientError, ValueError, TypeError, KeyError):
            return "UNKNOWN"

    def put_evidence(self, key, snapshot):
        def binary(value):
            if isinstance(value, bytes):
                return {"__sqs_binary__": base64.b64encode(value).decode()}
            raise TypeError("Unsupported evidence value")
        body = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=binary).encode()
        try:
            self.clients.s3.put_object(Bucket=self.settings.evidence_bucket, Key=key, Body=body,
                ContentType="application/json", ServerSideEncryption="aws:kms", SSEKMSKeyId=self.settings.kms_key_arn,
                Metadata={"sha256": sha(body)}, IfNoneMatch="*")
        except ClientError as error:
            if error.response["Error"]["Code"] not in {"PreconditionFailed", "412"}:
                raise
            existing = self.evidence(key)
            stable_fields = ["body", "messageAttributes", "originalMessageId", "queueUrl", "checksum"]
            if any(existing.get(field) != snapshot.get(field) for field in stable_fields):
                raise CloudProblem(409, "Existing immutable evidence does not match") from error
            # Receive counts change after a crash between S3 and index commits.
            # Preserve the first observed system attributes and bind the index
            # to that original envelope, rather than overwriting its evidence.
            body = json.dumps(existing, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=binary).encode()
        return sha(body)

    def evidence(self, key, expected_checksum=None):
        result = self.clients.s3.get_object(Bucket=self.settings.evidence_bucket, Key=key)
        body = result["Body"].read()
        if sha(body) != result.get("Metadata", {}).get("sha256") or (expected_checksum and sha(body) != expected_checksum):
            raise CloudProblem(409, "Evidence integrity check failed")
        def binary(value):
            return base64.b64decode(value["__sqs_binary__"]) if set(value) == {"__sqs_binary__"} else value
        return json.loads(body, object_hook=binary)


def runtime():
    from cloud.contracts import Settings
    settings = Settings.from_env()
    return Repository(settings, Clients.create(settings.region))
