"""Explicitly opted-in DLQ receive and encrypted snapshot; never deletes messages."""
from cloud.aws import runtime
from cloud.contracts import CloudProblem, sha, validate_event
from cloud.investigation import incident_writes


def collect(repo):
    response = repo.clients.sqs.receive_message(QueueUrl=repo.settings.dlq_url, MaxNumberOfMessages=10,
        WaitTimeSeconds=0, VisibilityTimeout=30, MessageAttributeNames=["All"], MessageSystemAttributeNames=["All"])
    indexed = []
    for message in response.get("Messages", []):
        identifier = sha(repo.settings.dlq_url + "\n" + message["MessageId"] + "\n" + sha(message["Body"]))
        try:
            existing = repo.get(repo.queue_pk, "MSG#" + identifier)
            if existing:
                indexed.append(identifier)
                continue
            snapshot = {"body": message["Body"], "messageAttributes": message.get("MessageAttributes", {}),
                        "systemAttributes": message.get("Attributes", {}), "originalMessageId": message["MessageId"],
                        "queueUrl": repo.settings.dlq_url, "checksum": sha(message["Body"])}
            key = "evidence/" + identifier + ".json"
            evidence_checksum = repo.put_evidence(key, snapshot)
            try:
                operation = validate_event(message["Body"])["operationId"]
                valid, error = True, "UnclassifiedFailure"
            except CloudProblem:
                operation, valid, error = None, False, "InvalidSchema"
            observed_at = int(repo.clock())
            item = {"pk": repo.queue_pk, "sk": "MSG#" + identifier, "id": identifier,
                    "operationId": operation, "valid": valid, "error": error, "checksum": snapshot["checksum"],
                    "evidenceKey": key, "evidenceChecksum": evidence_checksum, "originalMessageId": message["MessageId"], "observedAt": observed_at,
                    "sourceSentAt": int(message.get("Attributes", {}).get("SentTimestamp", "0")) // 1000,
                    "expires_at": observed_at + repo.settings.retention_days * 86400}
            try:
                repo.commit(incident_writes(repo, item), "collector", "SNAPSHOT_INDEXED", identifier,
                            detail={"incidentId": item["incidentId"]})
            except CloudProblem:
                if not repo.get(repo.queue_pk, item["sk"]):
                    raise
            indexed.append(identifier)
        finally:
            # Release the receive lease; this is a visibility mutation, not deletion.
            repo.clients.sqs.change_message_visibility(QueueUrl=repo.settings.dlq_url,
                ReceiptHandle=message["ReceiptHandle"], VisibilityTimeout=0)
    return {"indexed": indexed, "originalsRetained": True, "receiveChangesVisibility": True}


def handler(event, context):
    if event.get("acknowledgeVisibilityChange") is not True:
        raise CloudProblem(400, "Collection requires explicit acknowledgment of receive visibility changes")
    return collect(runtime())
