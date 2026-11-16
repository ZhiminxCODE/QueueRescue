"""AWS-backed planning, approvals, reservations and conservative outcome checks."""
import time

from cloud.aws import condition, new_item, update, wire
from cloud.contracts import CloudProblem, MAX_PLAN_MESSAGES, encode, sha, validate_event
from cloud.report_index import incident_links, link_writes


ATTEMPT_STATES = ["QUEUED", "LEASED", "SENT", "UNCERTAIN", "REVIEW", "SKIPPED", "VERIFIED", "UNVERIFIED", "CANCELLED", "FAILED"]


def required(repo, pk):
    item = repo.get(pk)
    if item is None:
        raise CloudProblem(404, "Resource not found")
    return item


def classify(repo, message):
    snapshot = repo.evidence(message["evidenceKey"], message["evidenceChecksum"])
    if sha(snapshot["body"]) != message["checksum"] or snapshot["queueUrl"] != repo.settings.dlq_url:
        return "BLOCKED", "Snapshot integrity or source mismatch"
    try:
        event = validate_event(snapshot["body"])
    except CloudProblem:
        return "BLOCKED", "Shipment schema rejected"
    if event["operationId"] != message.get("operationId") or len(snapshot["body"].encode()) > 200000:
        return "BLOCKED", "Invalid business identity or sandbox payload size"
    attrs = snapshot["messageAttributes"]
    if len(attrs) > 7 or set(attrs) & {"replayJobId", "replayAttemptId", "operationId"}:
        return "BLOCKED", "Original attributes conflict with replay identity or exceed attribute budget"
    status = repo.business_outcome(event["operationId"])
    if status == "COMPLETE":
        return "SKIP", "Business verifier confirms an existing shipment"
    if status == "NOT_STARTED":
        return "ELIGIBLE", "Business verifier confirms no shipment under operationId contract"
    return "REVIEW", "Business outcome is " + status


def create_plan(repo, actor, data, request):
    identifiers = data.get("messageIds")
    rate = data.get("rate", 5)
    if not isinstance(identifiers, list) or not 1 <= len(identifiers) <= MAX_PLAN_MESSAGES or any(not isinstance(i, str) for i in identifiers) or len(set(identifiers)) != len(identifiers):
        raise CloudProblem(400, "Select 1–25 distinct snapshot IDs explicitly")
    if type(rate) is not int or not 1 <= rate <= 25:
        raise CloudProblem(400, "Sandbox rate must be an integer from 1–25 messages/sec")
    if data.get("destination", repo.settings.replay_queue_url) != repo.settings.replay_queue_url:
        raise CloudProblem(403, "Destination is not allowlisted")
    seen, decisions, messages = set(), [], []
    for identifier in identifiers:
        message = repo.get(repo.queue_pk, "MSG#" + identifier)
        if not message or message["expires_at"] <= int(repo.clock()):
            raise CloudProblem(404, "Snapshot is missing or expired")
        messages.append(message)
        decision, reason = classify(repo, message)
        operation = message.get("operationId")
        if decision == "ELIGIBLE" and operation in seen:
            decision, reason = "SKIP", "Duplicate business operation in selection"
        if decision == "ELIGIBLE":
            seen.add(operation)
        decisions.append({"messageId": identifier, "operationId": operation, "decision": decision, "reason": reason,
                          "checksum": message["checksum"], "evidenceChecksum": message["evidenceChecksum"], "evidenceKey": message["evidenceKey"]})
    identifier = "plan-" + request[0][-24:]
    now = int(repo.clock())
    document = {"id": identifier, "version": 1, "createdAt": now, "expiresAt": now + 3600,
                "rate": rate, "destination": repo.settings.replay_queue_url,
                "consumerVersion": repo.settings.consumer_version, "idempotencyContract": "operationId-unique", "decisions": decisions}
    links = incident_links(repo, messages)
    item = {"pk": "PLAN#" + identifier, "sk": "META", "state": "READY", "createdBy": actor["id"], "incidentLinks": links,
            "document": document, "planHash": sha(encode(document)), "expiresAt": document["expiresAt"]}
    response = {"id": identifier, "state": "READY", "planHash": item["planHash"], "document": document}
    repo.commit([new_item(repo.table, item), new_item(repo.table, {"pk":"CATALOG#PLAN","sk":identifier,"id":identifier}),
                 *link_writes(repo, links, "PLAN", identifier)], actor["id"], "PLAN_CREATED", identifier, request=request, response=response)
    return response


def approve_plan(repo, actor, identifier, data, request):
    plan = required(repo, "PLAN#" + identifier)
    now = int(repo.clock())
    if plan["state"] != "READY" or plan["expiresAt"] <= now or sha(encode(plan["document"])) != plan["planHash"] or data.get("planHash") != plan["planHash"]:
        raise CloudProblem(409, "Review the intact, unexpired current plan hash")
    if actor["id"] == plan["createdBy"]:
        raise CloudProblem(403, "A different person must approve this plan")
    note = data.get("note")
    if not isinstance(note, str) or not 5 <= len(note.strip()) <= 1000:
        raise CloudProblem(400, "Approval note must contain 5–1000 characters")
    if not any(d["decision"] == "ELIGIBLE" for d in plan["document"]["decisions"]):
        raise CloudProblem(409, "No eligible operations in this plan")
    response = {"id": identifier, "state": "APPROVED", "planHash": plan["planHash"], "approvedBy": actor["id"]}
    actions = [update(repo.table, {"pk": plan["pk"], "sk": "META"},
        "SET #state=:approved,approvedBy=:actor,approvalNote=:note", "#state=:ready AND planHash=:hash AND expiresAt>:now",
        {":approved":"APPROVED",":actor":actor["id"],":note":note.strip(),":ready":"READY",":hash":plan["planHash"],":now":now}, {"#state":"state"})]
    repo.commit(actions, actor["id"], "PLAN_APPROVED", identifier, {"planHash": plan["planHash"]}, request, response)
    return response


def lock_key(repo, operation):
    return {"pk": "LOCK#" + sha(repo.settings.dlq_url + "\n" + operation), "sk": "META"}


def start_job(repo, actor, data, request):
    plan = required(repo, "PLAN#" + str(data.get("approvedPlanId", "")))
    now = int(repo.clock())
    if plan["state"] != "APPROVED" or plan["expiresAt"] <= now or sha(encode(plan["document"])) != plan["planHash"]:
        raise CloudProblem(409, "An intact unexpired approved plan is required")
    if plan["document"]["consumerVersion"] != repo.settings.consumer_version:
        raise CloudProblem(409, "Consumer deployment changed; create and approve a new plan")
    identifier = "job-" + request[0][-24:]
    job = {"pk":"JOB#" + identifier,"sk":"META","id":identifier,"planId":plan["document"]["id"],
           "planHash":plan["planHash"],"state":"RUNNING","generation":0,"cursor":0,"createdAt":now,"updatedAt":now}
    actions = [new_item(repo.table, job), new_item(repo.table, {"pk":"CATALOG#JOB","sk":identifier,"id":identifier}), update(repo.table, {"pk":plan["pk"],"sk":"META"}, "SET #state=:executing",
        "#state=:approved AND expiresAt>:now AND planHash=:hash", {":executing":"EXECUTING",":approved":"APPROVED",":now":now,":hash":plan["planHash"]}, {"#state":"state"})]
    eligible_ids = {d["messageId"] for d in plan["document"]["decisions"] if d["decision"] == "ELIGIBLE"}
    job_links = [link for link in plan.get("incidentLinks", []) if eligible_ids.intersection(link["messageIds"])]
    actions.extend(link_writes(repo, job_links, "JOB", identifier, plan["document"]["id"]))
    for decision in plan["document"]["decisions"]:
        if decision["decision"] != "ELIGIBLE":
            continue
        attempt_id = "attempt-" + sha(identifier + decision["messageId"])[:24]
        actions += [new_item(repo.table, {**lock_key(repo, decision["operationId"]), "jobId": identifier}),
                    new_item(repo.table, {"pk":job["pk"],"sk":"ATTEMPT#" + decision["messageId"],"id":attempt_id,
                                         "messageId":decision["messageId"],"operationId":decision["operationId"],"state":"QUEUED"})]
    response = {"id":identifier,"state":"RUNNING","generation":0}
    repo.commit(actions, actor["id"], "JOB_STARTED", identifier, {"planHash":plan["planHash"]}, request, response)
    return response


def ensure_execution(repo, job):
    name = job["id"] + "-" + str(job["generation"])
    payload = encode({"jobId":job["id"],"generation":job["generation"]})
    try:
        return repo.clients.states.start_execution(stateMachineArn=repo.settings.state_machine_arn, name=name, input=payload)["executionArn"]
    except repo.clients.states.exceptions.ExecutionAlreadyExists:
        # A completed Standard execution retains its unique name. It must never
        # be restarted as an unintended replay when a client repeats a request.
        return repo.settings.state_machine_arn.replace(":stateMachine:", ":execution:") + ":" + name


def job_detail(repo, identifier):
    job = required(repo, "JOB#" + identifier)
    attempts, _ = repo.query(job["pk"], "ATTEMPT#", limit=MAX_PLAN_MESSAGES)
    counts = {state: sum(a["state"] == state for a in attempts) for state in ATTEMPT_STATES}
    return {**job, "attempts":attempts,"counts":counts,"total":len(attempts)}


def release_lock(repo, attempt, identifier):
    return {"Delete": {"TableName":repo.table,"Key":wire(lock_key(repo, attempt["operationId"])),
                       "ConditionExpression":"jobId=:job","ExpressionAttributeValues":wire({":job":identifier})}}


def control_job(repo, actor, identifier, action, data, request):
    job = job_detail(repo, identifier)
    old = job["state"]
    targets = {"pause":("RUNNING","PAUSED"),"resume":("PAUSED","RUNNING")}
    if action == "cancel":
        if old in {"COMPLETED","CANCELLED","FAILED"}:
            raise CloudProblem(409, "Job is already terminal")
        target = "CANCELLED"
    elif action in targets and old == targets[action][0]:
        target = targets[action][1]
    else:
        raise CloudProblem(409, "Job does not permit this transition")
    if action == "resume" and any(job["counts"][s] for s in ["LEASED","UNCERTAIN"]):
        raise CloudProblem(409, "Reconcile uncertain sends before resuming")
    generation = job["generation"] + (1 if action == "resume" else 0)
    values = {":target":target,":old":old,":now":int(repo.clock()),":generation":generation,
              ":oldgeneration":job["generation"],":reason":str(data.get("reason", action))[:500]}
    actions = [update(repo.table, {"pk":job["pk"],"sk":"META"}, "SET #state=:target,updatedAt=:now,generation=:generation,reason=:reason",
        "#state=:old AND generation=:oldgeneration", values, {"#state":"state"})]
    if action == "cancel":
        for attempt in job["attempts"]:
            if attempt["state"] == "QUEUED":
                actions += [update(repo.table, {"pk":attempt["pk"],"sk":attempt["sk"]}, "SET #state=:cancelled", "#state=:queued",
                    {":cancelled":"CANCELLED",":queued":"QUEUED"}, {"#state":"state"}), release_lock(repo, attempt, identifier)]
    response = {"id":identifier,"state":target,"generation":generation}
    repo.commit(actions, actor["id"], "JOB_" + action.upper(), identifier, request=request, response=response)
    return response


def verify_job(repo, actor, identifier, request=None):
    job = job_detail(repo, identifier)
    attempts = job["attempts"]
    actions = []
    for attempt in attempts:
        if attempt["state"] not in {"SENT","UNCERTAIN","UNVERIFIED","REVIEW"}:
            continue
        status = repo.business_outcome(attempt["operationId"])
        state = "VERIFIED" if status == "COMPLETE" else "UNVERIFIED" if attempt["state"] in {"SENT","UNVERIFIED"} else "REVIEW"
        actions.append(update(repo.table, {"pk":attempt["pk"],"sk":attempt["sk"]}, "SET #state=:target,outcome=:outcome,checkedAt=:now",
            "#state=:old", {":target":state,":outcome":status,":now":int(repo.clock()),":old":attempt["state"]}, {"#state":"state"}))
        if state == "VERIFIED" and repo.get(**lock_key(repo, attempt["operationId"])):
            actions.append(release_lock(repo, attempt, identifier))
        attempt["state"] = state
    target = job["state"]
    if target in {"VERIFYING","PARTIAL"}:
        target = "COMPLETED" if all(a["state"] in {"VERIFIED","SKIPPED"} for a in attempts) else "PARTIAL"
    resolution = ",resolvedAt=if_not_exists(resolvedAt,:now)" if attempts and all(a["state"] in {"VERIFIED", "SKIPPED"} for a in attempts) else ""
    actions.append(update(repo.table, {"pk":job["pk"],"sk":"META"}, "SET #state=:target,updatedAt=:now" + resolution, "#state=:old AND generation=:generation",
        {":target":target,":now":int(repo.clock()),":old":job["state"],":generation":job["generation"]}, {"#state":"state"}))
    response = {"id":identifier,"state":target,"counts":{s:sum(a["state"]==s for a in attempts) for s in ATTEMPT_STATES}}
    repo.commit(actions, actor["id"], "OUTCOMES_VERIFIED", identifier, request=request, response=response)
    return response
