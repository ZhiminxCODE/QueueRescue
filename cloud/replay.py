"""Bounded replay step. A leased or uncertain send is never automatically repeated."""
import time
import uuid

from botocore.exceptions import BotoCoreError, ClientError

from cloud.aws import condition, runtime, update, wire
from cloud.contracts import CloudProblem, encode, sha
from cloud.recovery import classify, job_detail, lock_key, release_lock, required
from cloud.telemetry import emit, message_refs, reference, queue_arn


def attempt_refs(repo, identifier, attempt):
    return {"queueRef": reference("queue", queue_arn(repo.settings.replay_queue_url)),
            "operationRef": reference("operation", attempt["operationId"]),
            "jobRef": reference("job", identifier), "attemptRef": reference("attempt", attempt["id"])}


def worker_recovery(repo, identifier, generation):
    job = job_detail(repo, identifier)
    if job["generation"] != generation:
        return {"jobId":identifier,"generation":generation,"continue":False}
    actions = []
    for attempt in job["attempts"]:
        if attempt["state"] == "LEASED":
            emit("replay", "REPLAY_WORKER_INTERRUPTED", clock=repo.clock, **attempt_refs(repo, identifier, attempt))
            actions.append(update(repo.table, {"pk":attempt["pk"],"sk":attempt["sk"]},
                "SET #state=:uncertain,detail=:detail", "#state=:leased",
                {":uncertain":"UNCERTAIN",":detail":"Worker interrupted before durable send acknowledgment",":leased":"LEASED"}, {"#state":"state"}))
    target = job["state"] if job["state"] in {"CANCELLED","COMPLETED","FAILED"} else "PAUSED"
    actions.append(update(repo.table, {"pk":job["pk"],"sk":"META"}, "SET #state=:target,reason=:reason,updatedAt=:now",
        "#state=:old AND generation=:generation", {":target":target,":old":job["state"],":generation":generation,
        ":reason":"Worker interruption; reconcile before resuming",":now":int(repo.clock())}, {"#state":"state"}))
    repo.commit(actions, "replay-worker", "WORKER_RECONCILIATION_REQUIRED", identifier)
    return {"jobId":identifier,"generation":generation,"continue":False}


def step(repo, identifier, generation, max_sends=5, sleep=time.sleep, monotonic=time.monotonic, after_send=None):
    started = monotonic()
    sends = 0
    for _ in range(max_sends):
        job = required(repo, "JOB#" + identifier)
        if job["state"] != "RUNNING" or job["generation"] != generation:
            return {"jobId":identifier,"generation":generation,"continue":False}
        plan = required(repo, "PLAN#" + job["planId"])
        document = plan["document"]
        if sha(encode(document)) != job["planHash"] or plan["expiresAt"] <= int(repo.clock()) or document["destination"] != repo.settings.replay_queue_url or document["consumerVersion"] != repo.settings.consumer_version:
            return worker_recovery(repo, identifier, generation)
        candidates = [d for d in document["decisions"] if d["decision"] == "ELIGIBLE"]
        cursor = job["cursor"]
        job_key = {"pk":job["pk"],"sk":"META"}
        job_condition = "#state=:running AND generation=:generation AND #cursor=:cursor"
        job_values = {":running":"RUNNING",":generation":generation,":cursor":cursor}
        if cursor >= len(candidates):
            repo.commit([update(repo.table, job_key, "SET #state=:verifying,updatedAt=:now", job_condition,
                {**job_values,":verifying":"VERIFYING",":now":int(repo.clock())}, {"#state":"state","#cursor":"cursor"})],
                "replay-worker", "SEND_PHASE_FINISHED", identifier)
            return {"jobId":identifier,"generation":generation,"continue":False}
        decision = candidates[cursor]
        attempt = repo.get(job["pk"], "ATTEMPT#" + decision["messageId"])
        attempt_key = {"pk":attempt["pk"],"sk":attempt["sk"]}
        advance = update(repo.table, job_key, "SET #cursor=:next,updatedAt=:now", job_condition,
            {**job_values,":next":cursor + 1,":now":int(repo.clock())}, {"#state":"state","#cursor":"cursor"})
        if attempt["state"] in {"LEASED","UNCERTAIN"}:
            return worker_recovery(repo, identifier, generation)
        if attempt["state"] != "QUEUED":
            repo.commit([advance], "replay-worker", "ACKNOWLEDGED_ATTEMPT_RECONCILED", attempt["id"])
            continue
        message = repo.get(repo.queue_pk, "MSG#" + decision["messageId"])
        if not message or message["checksum"] != decision["checksum"] or message["evidenceChecksum"] != decision["evidenceChecksum"] or message["expires_at"] <= int(repo.clock()):
            disposition, reason = "BLOCKED", "Approved evidence is missing, changed, or expired"
        else:
            disposition, reason = classify(repo, message)
        if disposition != "ELIGIBLE":
            target = "SKIPPED" if disposition == "SKIP" else "REVIEW"
            repo.commit([advance, update(repo.table, attempt_key, "SET #state=:target,detail=:detail", "#state=:queued",
                {":target":target,":detail":reason,":queued":"QUEUED"}, {"#state":"state"}), release_lock(repo, attempt, identifier)],
                "replay-worker", "PREFLIGHT_EXCLUDED", attempt["id"], {"disposition":target})
            continue
        token = uuid.uuid4().hex
        repo.commit([condition(repo.table, job_key, job_condition, job_values, {"#state":"state","#cursor":"cursor"}),
            condition(repo.table, lock_key(repo, attempt["operationId"]), "jobId=:job", {":job":identifier}),
            update(repo.table, attempt_key, "SET #state=:leased,leaseToken=:token,leaseUntil=:until", "#state=:queued",
                {":leased":"LEASED",":token":token,":until":int(repo.clock()) + 60,":queued":"QUEUED"}, {"#state":"state"})],
            "replay-worker", "SEND_INTENT_RECORDED", attempt["id"])
        current = required(repo, job["pk"])
        if current["state"] != "RUNNING" or current["generation"] != generation:
            # This lease has not called SQS; safely return it to the queue, or cancel it.
            target = "CANCELLED" if current["state"] == "CANCELLED" else "QUEUED"
            actions = [update(repo.table, attempt_key, "SET #state=:target", "#state=:leased AND leaseToken=:token",
                {":target":target,":leased":"LEASED",":token":token}, {"#state":"state"})]
            if target == "CANCELLED":
                actions.append(release_lock(repo, attempt, identifier))
            repo.commit(actions, "replay-worker", "UNSENT_LEASE_RELEASED", attempt["id"])
            return {"jobId":identifier,"generation":generation,"continue":False}
        snapshot = repo.evidence(decision["evidenceKey"], decision["evidenceChecksum"])
        attrs = dict(snapshot["messageAttributes"])
        attrs.update({key:{"DataType":"String","StringValue":value} for key,value in
            {"replayJobId":identifier,"replayAttemptId":attempt["id"],"operationId":attempt["operationId"]}.items()})
        refs = {**message_refs(snapshot["originalMessageId"], attempt["operationId"], snapshot["messageAttributes"]),
                **attempt_refs(repo, identifier, attempt), "snapshotRef": reference("snapshot", decision["messageId"])}
        emit("replay", "REPLAY_SEND_INTENT", clock=repo.clock, **refs)
        try:
            result = repo.clients.sqs.send_message(QueueUrl=repo.settings.replay_queue_url, MessageBody=snapshot["body"], MessageAttributes=attrs)
        except (BotoCoreError, ClientError):
            emit("replay", "REPLAY_SEND_UNCERTAIN", clock=repo.clock, outcome="UNKNOWN", **refs)
            return worker_recovery(repo, identifier, generation)
        emit("replay", "REPLAY_SEND_ACCEPTED", clock=repo.clock,
             **{**refs, "messageRef": reference("message", result["MessageId"])})
        if after_send is not None:
            after_send()
        # Cancellation never erases an already-sent event. Advance the cursor
        # without changing the current operator-controlled job state.
        acknowledge = update(repo.table, job_key, "SET #cursor=:next,updatedAt=:now", "generation=:generation AND #cursor=:cursor",
            {":generation":generation,":cursor":cursor,":next":cursor + 1,":now":int(repo.clock())}, {"#cursor":"cursor"})
        repo.commit([acknowledge, update(repo.table, attempt_key,
            "SET #state=:sent,sentAt=:now,sqsMessageId=:message", "#state=:leased AND leaseToken=:token",
            {":sent":"SENT",":now":int(repo.clock()),":message":result["MessageId"],":leased":"LEASED",":token":token}, {"#state":"state"})],
            "replay-worker", "DELIVERY_ACKNOWLEDGED", attempt["id"])
        sends += 1
        sleep(max(0, sends / document["rate"] - (monotonic() - started)))
    return {"jobId":identifier,"generation":generation,"continue":True}


def handler(event, context):
    repo = runtime()
    if event.get("action") == "recover":
        return worker_recovery(repo, event["jobId"], event["generation"])
    return step(repo, event["jobId"], event["generation"])
