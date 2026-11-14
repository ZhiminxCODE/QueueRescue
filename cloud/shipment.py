"""Synthetic business service: conditional shipment creation and status verifier."""
import os
import time
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from cloud.aws import unwire, wire
from cloud.contracts import CloudProblem, validate_event


def process_record(ddb, table, record, replay_queue_arn):
    event = validate_event(record["body"])
    operation = event["operationId"]
    is_replay = record.get("eventSourceARN") == replay_queue_arn
    mode = record.get("messageAttributes", {}).get("qrDemoMode", {}).get("stringValue", "TIMEOUT_BEFORE")
    key = wire({"pk": "OP#" + operation, "sk": "META"})
    state = ddb.get_item(TableName=table, Key=key, ConsistentRead=True).get("Item")
    state = unwire(state) if state else {"status": "UNKNOWN"}
    if state["status"] == "COMPLETE":
        if not is_replay and mode == "TIMEOUT_AFTER":
            raise TimeoutError("Synthetic warehouse timeout after business effect")
        return
    if state["status"] != "NOT_STARTED":
        raise CloudProblem(409, "Business state is not eligible")
    # Fault injection is based on the AWS event source ARN, never a spoofable attribute.
    if not is_replay and mode == "TIMEOUT_BEFORE":
        raise TimeoutError("Synthetic warehouse timeout before business effect")
    try:
        ddb.update_item(TableName=table, Key=key,
            UpdateExpression="SET #status=:complete,shipmentId=:shipment,completedAt=:now",
            ConditionExpression="#status=:not_started", ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues=wire({":complete": "COMPLETE", ":not_started": "NOT_STARTED",
                ":shipment": "shp-" + uuid.uuid4().hex, ":now": int(time.time())}))
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        latest = ddb.get_item(TableName=table, Key=key, ConsistentRead=True).get("Item")
        if not latest or unwire(latest).get("status") != "COMPLETE":
            raise
    if not is_replay and mode == "TIMEOUT_AFTER":
        raise TimeoutError("Synthetic warehouse timeout after business effect")


def consumer(event, context):
    ddb = boto3.client("dynamodb", config=Config(retries={"total_max_attempts": 1}))
    failures = []
    for record in event.get("Records", []):
        try:
            process_record(ddb, os.environ["QR_BUSINESS_TABLE"], record, os.environ["QR_REPLAY_QUEUE_ARN"])
        except Exception:
            failures.append({"itemIdentifier": record["messageId"]})
    # No payloads or exception text are logged. Lambda retries only failed records.
    return {"batchItemFailures": failures}


def verifier(event, context):
    operation = event.get("operationId")
    if not isinstance(operation, str) or not 0 < len(operation) <= 256:
        return {"status": "UNKNOWN"}
    ddb = boto3.client("dynamodb")
    result = ddb.get_item(TableName=os.environ["QR_BUSINESS_TABLE"],
        Key=wire({"pk": "OP#" + operation, "sk": "META"}), ConsistentRead=True).get("Item")
    if not result:
        return {"status": "UNKNOWN"}
    row = unwire(result)
    status = row.get("status")
    if status not in {"COMPLETE", "NOT_STARTED", "IN_PROGRESS", "UNKNOWN"} or (status == "COMPLETE" and not row.get("shipmentId")):
        status = "UNKNOWN"
    return {"status": status, "operationId": operation, "shipmentId": row.get("shipmentId"), "checkedAt": int(time.time())}
