"""Synthetic business service: conditional shipment creation and status verifier."""
import os
import time
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from cloud.aws import unwire, wire
from cloud.contracts import CloudProblem, validate_event
from cloud.telemetry import emit, record_refs, reference


class DemoTimeout(TimeoutError):
    def __init__(self, outcome):
        self.outcome = outcome
        super().__init__(outcome)


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
            raise DemoTimeout("TIMEOUT_AFTER")
        return
    if state["status"] != "NOT_STARTED":
        raise CloudProblem(409, "Business state is not eligible")
    # Fault injection is based on the AWS event source ARN, never a spoofable attribute.
    if not is_replay and mode == "TIMEOUT_BEFORE":
        raise DemoTimeout("TIMEOUT_BEFORE")
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
        raise DemoTimeout("TIMEOUT_AFTER")


def consumer(event, context):
    ddb = boto3.client("dynamodb", config=Config(retries={"total_max_attempts": 1}))
    failures = []
    for record in event.get("Records", []):
        refs = record_refs(record, os.environ["QR_REPLAY_QUEUE_ARN"])
        refs["requestRef"] = reference("request", getattr(context, "aws_request_id", None))
        try:
            process_record(ddb, os.environ["QR_BUSINESS_TABLE"], record, os.environ["QR_REPLAY_QUEUE_ARN"])
        except Exception as error:
            failures.append({"itemIdentifier": record["messageId"]})
            outcome = (error.outcome if isinstance(error, DemoTimeout) else
                       "INVALID_SCHEMA" if isinstance(error, CloudProblem) and error.status == 422 else
                       "BUSINESS_STATE_BLOCKED" if isinstance(error, CloudProblem) else
                       "AWS_ERROR" if isinstance(error, (ClientError, BotoCoreError)) else "UNEXPECTED_ERROR")
            emit("consumer", "CONSUMER_FAILED", outcome=outcome, **refs)
        else:
            emit("consumer", "CONSUMER_SUCCEEDED", outcome="HANDLER_RETURNED", **refs)
    # No payloads or exception text are logged. Lambda retries only failed records.
    return {"batchItemFailures": failures}


def _verify(event):
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


def verifier(event, context):
    refs = {"operationRef": reference("operation", event.get("operationId")),
            "requestRef": reference("request", getattr(context, "aws_request_id", None))}
    try:
        result = _verify(event)
    except Exception:
        emit("verifier", "VERIFICATION_UNAVAILABLE", outcome="UNKNOWN", **refs)
        raise
    emit("verifier", "VERIFICATION_CHECKED", outcome=result["status"], **refs)
    return result
