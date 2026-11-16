"""Versioned, allowlisted telemetry. Never log bodies, attributes or exception text."""
import json
import re
import time

from cloud.contracts import sha


EVENTS = {
    "collector": {"SNAPSHOT_INDEXED"},
    "consumer": {"CONSUMER_SUCCEEDED", "CONSUMER_FAILED"},
    "replay": {"REPLAY_SEND_INTENT", "REPLAY_SEND_ACCEPTED", "REPLAY_SEND_UNCERTAIN", "REPLAY_WORKER_INTERRUPTED"},
    "verifier": {"VERIFICATION_CHECKED", "VERIFICATION_UNAVAILABLE"},
    "api": {"API_COMPLETED"},
}
OUTCOMES = {"HANDLER_RETURNED", "INVALID_SCHEMA", "BUSINESS_STATE_BLOCKED", "TIMEOUT_BEFORE", "TIMEOUT_AFTER",
            "AWS_ERROR", "UNEXPECTED_ERROR", "COMPLETE", "NOT_STARTED", "IN_PROGRESS", "UNKNOWN"}
REF_FIELDS = {"queueRef", "messageRef", "operationRef", "correlationRef", "jobRef", "attemptRef", "snapshotRef", "requestRef"}


def reference(kind, value):
    try:
        return sha(kind + ":" + value) if isinstance(value, str) and 0 < len(value) <= 2048 else None
    except UnicodeError:
        return None


def queue_arn(url):
    match = re.fullmatch(r"https://sqs\.([a-z0-9-]+)\.amazonaws\.com/(\d{12})/([A-Za-z0-9_-]+)", url)
    return "arn:aws:sqs:" + ":".join(match.groups()) if match else ""


def message_refs(message_id, operation=None, attributes=None):
    attrs = attributes if isinstance(attributes, dict) else {}
    correlation = attrs.get("correlationId", {})
    correlation = correlation if isinstance(correlation, dict) else {}
    values = {"messageRef": reference("message", message_id), "operationRef": reference("operation", operation),
              "correlationRef": reference("correlation", correlation.get("StringValue", correlation.get("stringValue")))}
    return {key: value for key, value in values.items() if value}


def record_refs(record, replay_queue_arn):
    try:
        body = json.loads(record.get("body", ""))
    except (ValueError, TypeError, RecursionError):
        body = None
    attrs = record.get("messageAttributes", {})
    refs = message_refs(record.get("messageId"), body.get("operationId") if isinstance(body, dict) else None, attrs)
    refs["queueRef"] = reference("queue", record.get("eventSourceARN"))
    # Replay identity is meaningful only for the configured replay event source.
    if record.get("eventSourceARN") == replay_queue_arn and isinstance(attrs, dict):
        for field, attr, kind in (("jobRef", "replayJobId", "job"), ("attemptRef", "replayAttemptId", "attempt")):
            value = attrs.get(attr, {})
            refs[field] = reference(kind, value.get("stringValue") if isinstance(value, dict) else None)
    return refs


def sanitized(row):
    """This same schema validates emitted records and untrusted log responses."""
    if (not isinstance(row, dict) or type(row.get("telemetryVersion")) is not int or row["telemetryVersion"] != 1
            or not isinstance(row.get("component"), str) or row["component"] not in EVENTS
            or not isinstance(row.get("event"), str) or row["event"] not in EVENTS[row["component"]]
            or type(row.get("at")) is not int or not 0 <= row["at"] <= 253402300799000):
        return None
    public = {key: row[key] for key in ("telemetryVersion", "component", "event", "at")}
    for key in REF_FIELDS:
        value = row.get(key)
        if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value):
            public[key] = value
    if isinstance(row.get("outcome"), str) and row["outcome"] in OUTCOMES:
        public["outcome"] = row["outcome"]
    if type(row.get("statusCode")) is int and 100 <= row["statusCode"] <= 599:
        public["statusCode"] = row["statusCode"]
    return public


def emit(component, event, clock=time.time, **fields):
    # Observability must not turn an accepted send or committed effect into a
    # retry. Logging failures never change the recovery protocol's outcome.
    try:
        row = sanitized({**fields, "telemetryVersion": 1, "component": component, "event": event, "at": int(clock() * 1000)})
        if row:
            print(json.dumps(row, sort_keys=True, separators=(",", ":")), flush=True)
    except Exception:
        pass
