"""Bounded incident reports from recorded evidence, never live recovery actions."""
import re

from cloud.contracts import CloudProblem, MAX_PLAN_MESSAGES, encode, sha
from cloud.investigation import CATEGORIES, incident_detail
from cloud.log_evidence import lookup
from cloud.recovery import ATTEMPT_STATES
from cloud.report_costs import cost_section
from cloud.telemetry import reference


MAX_MEMBERS, MAX_PLANS, MAX_JOBS, MAX_COSTS, MAX_READS = 100, 5, 5, 10, 32


def valid_id(value, kind="message"):
    pattern = r"[a-f0-9]{64}" if kind == "message" else kind + r"-[a-f0-9]{24}"
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def recorded_time(value, now):
    return value if type(value) is int and 0 <= value <= now else None


class Reads:
    def __init__(self, repo):
        self.repo, self.count, self.issues = repo, 0, set()

    def __getattr__(self, name):
        return getattr(self.repo, name)

    def get(self, *args, **kwargs):
        if self.count >= MAX_READS:
            self.issues.add("READ_BUDGET_REACHED")
            return None
        self.count += 1
        return self.repo.get(*args, **kwargs)

    def query(self, *args, **kwargs):
        if self.count >= MAX_READS:
            self.issues.add("READ_BUDGET_REACHED")
            return [], True
        self.count += 1
        return self.repo.query(*args, **kwargs)


def report(repo, identifier, query):
    if (set(query) - {"evidenceMessageId", "jobId", "attemptId", "from", "to"}
            or any(not isinstance(value, str) for value in query.values())
            or (query and not valid_id(query.get("evidenceMessageId")))):
        raise CloudProblem(400, "Select an exact incident evidence sample; arbitrary report queries are unsupported")
    reads = Reads(repo)
    incident = incident_detail(reads, identifier)
    now, pk = int(repo.clock()), "INCIDENT#" + identifier
    members, member_next = reads.query(pk, "MSG#", limit=MAX_MEMBERS)
    plan_refs, plan_next = reads.query(pk, "PLAN#", limit=MAX_PLANS)
    job_refs, job_next = reads.query(pk, "JOB#", limit=MAX_JOBS)
    costs, cost_next = reads.query(pk, "COST#", limit=MAX_COSTS, descending=True)
    for next_key, code in ((member_next, "MEMBER_LIMIT_REACHED"), (plan_next, "PLAN_LIMIT_REACHED"), (job_next, "JOB_LIMIT_REACHED")):
        if next_key:
            reads.issues.add(code)
    rows, evidence, plans, jobs, decisions = {}, [], {}, [], {}
    for member in members:
        message_id = member.get("id")
        if not valid_id(message_id):
            reads.issues.add("INVALID_MEMBER_METADATA")
            continue
        if member.get("reportIndexVersion") != 1:
            reads.issues.add("LEGACY_HISTORY_NOT_INDEXED")
        rows[message_id] = {"id": message_id, "operationRef": reference("operation", member.get("operationId")),
            "schemaValid": member.get("valid") if type(member.get("valid")) is bool else None,
            "metadataAvailable": True, "snapshotAccessExpired": member.get("expires_at", 0) <= now,
            "attempts": [], "evidenceIds": ["snapshot:" + message_id]}
        evidence.append({"id": "snapshot:" + message_id, "kind": "SNAPSHOT_METADATA", "messageId": message_id,
            "observedAt": member.get("observedAt"), "bodyRead": False, "storageAvailability": "NOT_CHECKED",
            "bodyChecksum": member.get("checksum") if valid_id(member.get("checksum")) else None,
            "snapshotChecksum": member.get("evidenceChecksum") if valid_id(member.get("evidenceChecksum")) else None})

    def linked_ids(link):
        values = link.get("messageIds", [])
        if (link.get("reportIndexVersion") != 1 or not isinstance(values, list) or not 1 <= len(values) <= MAX_PLAN_MESSAGES
                or any(not valid_id(value) for value in values) or len(set(values)) != len(values)):
            reads.issues.add("INVALID_REPORT_INDEX")
            return []
        return values

    def load_plan(plan_id):
        if not valid_id(plan_id, "plan"):
            reads.issues.add("INVALID_PLAN_REFERENCE")
            return None
        if plan_id in plans:
            return plans[plan_id]
        plan = reads.get("PLAN#" + plan_id)
        document = plan.get("document", {}) if plan else {}
        selected = document.get("decisions")
        if (not plan or document.get("id") != plan_id or sha(encode(document)) != plan.get("planHash")
                or document.get("destination") != repo.settings.replay_queue_url
                or not isinstance(selected, list) or not 1 <= len(selected) <= MAX_PLAN_MESSAGES
                or any(not isinstance(d, dict) or not valid_id(d.get("messageId")) or d.get("decision") not in {"ELIGIBLE", "SKIP", "REVIEW", "BLOCKED"} for d in selected)
                or len({d["messageId"] for d in selected}) != len(selected)):
            reads.issues.add("MISSING_OR_INVALID_PLAN")
            plans[plan_id] = None
            return None
        plans[plan_id] = plan
        evidence.append({"id": "plan:" + plan_id, "kind": "PLAN_DOCUMENT", "planId": plan_id,
                         "planHash": plan["planHash"], "integrity": "HASH_MATCHED", "createdAt": document.get("createdAt"),
                         "separateApprovalRecorded": bool(plan.get("approvedBy") and plan.get("approvedBy") != plan.get("createdBy"))})
        return plan

    def associate(plan, link):
        ids = linked_ids(link)
        associations = plan.get("incidentLinks", [])
        declared = next((value for value in associations if value.get("incidentId") == identifier), None)
        if not declared or set(declared.get("messageIds", [])) != set(ids):
            reads.issues.add("PLAN_INCIDENT_ASSOCIATION_MISMATCH")
            return {}
        matched = {}
        for decision in plan["document"]["decisions"]:
            message_id = decision["messageId"]
            if message_id not in ids:
                continue
            member = next((value for value in members if value.get("id") == message_id), None)
            if member and (member.get("checksum") != decision.get("checksum") or member.get("evidenceChecksum") != decision.get("evidenceChecksum")):
                reads.issues.add("SNAPSHOT_PLAN_CHECKSUM_MISMATCH")
                continue
            if member and member.get("operationId") != decision.get("operationId"):
                reads.issues.add("SNAPSHOT_PLAN_IDENTITY_MISMATCH")
                continue
            if message_id not in rows:
                reads.issues.add("SNAPSHOT_METADATA_MISSING")
                rows[message_id] = {"id": message_id, "operationRef": reference("operation", decision.get("operationId")),
                    "schemaValid": None, "metadataAvailable": False, "snapshotAccessExpired": True, "attempts": [], "evidenceIds": []}
            row = rows[message_id]
            row["evidenceIds"] = sorted(set(row["evidenceIds"] + ["plan:" + plan["document"]["id"]]))
            decisions.setdefault(message_id, []).append(decision["decision"])
            matched[message_id] = decision
        if set(matched) != set(ids):
            reads.issues.add("PLAN_MEMBER_ASSOCIATION_INCOMPLETE")
        return matched

    for link in plan_refs:
        plan = load_plan(link.get("id"))
        if plan:
            associate(plan, link)
    for link in job_refs:
        job_id = link.get("id")
        if not valid_id(job_id, "job"):
            reads.issues.add("INVALID_JOB_REFERENCE")
            continue
        job = reads.get("JOB#" + job_id)
        plan = load_plan(job.get("planId")) if job else None
        if (not job or not plan or job.get("planHash") != plan["planHash"] or link.get("planId") != job.get("planId")
                or job.get("state") not in {"RUNNING", "PAUSED", "VERIFYING", "PARTIAL", "COMPLETED", "CANCELLED", "FAILED"}
                or recorded_time(job.get("createdAt"), now) is None
                or not plan.get("approvedBy") or plan.get("approvedBy") == plan.get("createdBy")):
            reads.issues.add("MISSING_OR_INVALID_JOB_APPROVAL")
            continue
        related = associate(plan, link)
        attempts, attempt_next = reads.query("JOB#" + job_id, "ATTEMPT#", limit=MAX_PLAN_MESSAGES)
        after = reads.get("JOB#" + job_id)
        stable = after == job and not attempt_next
        if not stable:
            reads.issues.add("JOB_CHANGED_OR_ATTEMPTS_TRUNCATED")
        expected = {key for key, value in related.items() if value["decision"] == "ELIGIBLE"}
        found = set()
        for attempt in attempts:
            message_id = attempt.get("messageId")
            decision = related.get(message_id)
            if not decision:
                continue  # A mixed-incident job must not leak other members.
            if (message_id in found or not valid_id(attempt.get("id"), "attempt") or attempt.get("state") not in ATTEMPT_STATES
                    or decision["decision"] != "ELIGIBLE" or attempt.get("operationId") != decision.get("operationId")):
                reads.issues.add("INVALID_ATTEMPT_LEDGER")
                continue
            found.add(message_id)
            checked = recorded_time(attempt.get("checkedAt"), now)
            if checked is not None and checked < job["createdAt"]:
                checked = None
            outcome = attempt.get("outcome")
            outcome = outcome if isinstance(outcome, str) and outcome in {"COMPLETE", "NOT_STARTED", "IN_PROGRESS", "UNKNOWN"} else None
            if attempt["state"] == "VERIFIED" and (outcome != "COMPLETE" or checked is None):
                reads.issues.add("VERIFIED_STATE_WITHOUT_COMPLETION_PROOF")
            value = {"id": attempt["id"], "jobId": job_id, "state": attempt["state"], "outcome": outcome,
                     "checkedAt": checked, "stableRead": stable}
            rows[message_id]["attempts"].append(value)
            rows[message_id]["evidenceIds"].append("attempt:" + attempt["id"])
            evidence.append({"id": "attempt:" + attempt["id"], "kind": "ATTEMPT_LEDGER", "messageId": message_id, **value})
        if found != expected:
            reads.issues.add("ATTEMPT_MEMBERS_MISSING")
        jobs.append({"id": job_id, "state": job.get("state"), "createdAt": recorded_time(job.get("createdAt"), now),
                     "resolvedAt": recorded_time(job.get("resolvedAt"), now), "stableRead": stable})

    counts = {key: 0 for key in ("recovered", "skipped", "blocked", "unresolved")}
    checked_operations, completed_operations, attempted_operations = set(), set(), set()
    for row in rows.values():
        attempts = row["attempts"]
        operation = row["operationRef"]
        if operation and attempts:
            attempted_operations.add(operation)
        checked = [a for a in attempts if a["stableRead"] and a["checkedAt"] is not None and a["outcome"] is not None]
        if checked and operation:
            checked_operations.add(operation)
        completed = [a for a in checked if a["state"] == "VERIFIED" and a["outcome"] == "COMPLETE"]
        conflicting = bool(completed and any(a["outcome"] != "COMPLETE" and a["checkedAt"] >= max(c["checkedAt"] for c in completed) for a in checked))
        if conflicting:
            reads.issues.add("CONFLICTING_DOMAIN_EVIDENCE")
        if completed and not conflicting:
            disposition, reason = "recovered", "RECORDED_DOMAIN_COMPLETE"
            if operation:
                completed_operations.add(operation)
        elif any(a["state"] != "SKIPPED" or not a["stableRead"] for a in attempts) or conflicting:
            disposition, reason = "unresolved", "COMPLETION_NOT_ESTABLISHED"
        elif "SKIP" in decisions.get(row["id"], []) or any(a["state"] == "SKIPPED" for a in attempts):
            disposition, reason = "skipped", "RECORDED_SKIP_DECISION"
        elif row["schemaValid"] is False or "BLOCKED" in decisions.get(row["id"], []):
            disposition, reason = "blocked", "RECORDED_SCHEMA_OR_PLAN_BLOCK"
        else:
            disposition, reason = "unresolved", "NO_COMPLETION_EVIDENCE"
        row.update(disposition=disposition.upper(), reasonCode=reason, evidenceIds=sorted(set(row["evidenceIds"])))
        counts[disposition] += 1
    latest = reads.get(repo.queue_pk, "INC#" + identifier)
    if not latest or latest.get("collectedCount") != incident["collectedCount"]:
        reads.issues.add("INCIDENT_CHANGED_DURING_READ")
    historical = max(incident["collectedCount"], (latest or {}).get("collectedCount", 0), len(rows))
    unobserved = historical - len(rows)
    counts["unresolved"] += unobserved
    if unobserved:
        reads.issues.add("MEMBER_HISTORY_UNAVAILABLE")
    if incident.get("reportIndexVersion") != 1:
        reads.issues.add("LEGACY_HISTORY_NOT_INDEXED")
    logs = {"status": "NOT_REQUESTED", "items": [], "searchComplete": False, "sources": []}
    if query:
        message_id = query["evidenceMessageId"]
        if message_id not in rows:
            raise CloudProblem(404, "Evidence sample is outside the bounded incident membership")
        try:
            logs = lookup(repo, message_id, {key: value for key, value in query.items() if key != "evidenceMessageId"})
        except CloudProblem as error:
            if error.status == 400:
                raise
            logs = {"status": "UNAVAILABLE", "items": [], "searchComplete": False, "sources": [], "reasonCode": "SAMPLE_EVIDENCE_UNAVAILABLE"}
        for item in logs["items"]:
            evidence.append({"id": "log:" + sha(encode(item))[:24], "kind": "CORRELATED_TELEMETRY", "messageId": message_id, **item})
    hypotheses = []
    schema_refs = ["snapshot:" + row["id"] for row in rows.values() if row["schemaValid"] is False]
    if schema_refs:
        hypotheses.append({"code": "SCHEMA_REJECTION_RECORDED", "confidence": "SUPPORTED_OBSERVATION",
            "text": "The collector recorded shipment-schema rejection for these snapshots. This does not establish every upstream failure cause.", "evidenceIds": schema_refs})
    for outcome, text in (("TIMEOUT_BEFORE", "The demo consumer reported a timeout before its business effect."),
                          ("TIMEOUT_AFTER", "The demo consumer reported a timeout after its business effect; handler failure can coexist with a completed shipment."),
                          ("AWS_ERROR", "The consumer reported an AWS request failure; the underlying service cause is not established.")):
        refs = [item["id"] for item in evidence if item["kind"] == "CORRELATED_TELEMETRY" and item.get("outcome") == outcome]
        if refs:
            hypotheses.append({"code": outcome + "_OBSERVED", "confidence": "SUPPORTED_OBSERVATION", "text": text, "evidenceIds": refs})
    if not hypotheses:
        hypotheses.append({"code": "CAUSE_NOT_ESTABLISHED", "confidence": "INCONCLUSIVE",
            "text": "Grouping, send acknowledgments and recovery outcomes alone do not establish a root cause.", "evidenceIds": []})
    started = min((job["createdAt"] for job in jobs if job["createdAt"] is not None), default=None)
    resolved = max((job["resolvedAt"] for job in jobs if job["resolvedAt"] is not None), default=None)
    duration_complete = bool(jobs) and all(job["resolvedAt"] is not None and job["stableRead"] for job in jobs) and not reads.issues
    if resolved is not None and (started is None or resolved < started):
        resolved, duration_complete = None, False
    complete = not reads.issues
    percentage = round(100 * len(completed_operations) / len(attempted_operations), 2) if attempted_operations else None
    if percentage is not None and percentage.is_integer():
        percentage = int(percentage)  # Preserve canonical JSON through browser number serialization.
    result = {"reportVersion": 1, "generatedAt": now, "scope": "INDEXED_INCIDENT_SAMPLE", "region": repo.settings.region, "queueRef": repo.queue_pk,
        "incident": {key: incident[key] for key in ("id", "category", "label", "consumerVersion", "windowStart", "windowEnd", "expires_at", "collectedCount")},
        "coverage": {"completeForIndexedHistory": complete, "issues": sorted(reads.issues), "indexedSnapshotCount": historical,
            "representedSnapshots": len(rows), "unobservedSnapshots": unobserved, "plansRead": sum(plan is not None for plan in plans.values()),
            "jobsRead": len(jobs), "metadataReads": reads.count, "metadataReadLimit": MAX_READS,
            "logEvidenceSampled": bool(query), "logEvidenceCompleteForIncident": False},
        "summary": f"This indexed incident contains {historical} snapshots: {counts['recovered']} recovered with recorded domain completion, {counts['skipped']} skipped, {counts['blocked']} blocked, and {counts['unresolved']} unresolved. It is not a complete queue inventory.",
        "rootCauseEstablished": False, "counts": counts, "rows": sorted(rows.values(), key=lambda row: row["id"]), "jobs": jobs,
        "verification": {"unit": "DISTINCT_ATTEMPTED_OPERATIONS", "attemptedOperations": len(attempted_operations),
            "checkedOperations": len(checked_operations), "completeOperations": len(completed_operations),
            "completePercent": percentage,
            "liveVerificationPerformed": False},
        "duration": {"scope": "INCLUDED_REPLAY_JOBS", "startedAt": started, "resolvedAt": resolved if duration_complete else None,
            "resolvedDurationSeconds": resolved - started if duration_complete and started is not None else None,
            "elapsedSeconds": now - started if started is not None else None, "basis": "RECORDED_JOB_RESOLUTION" if duration_complete else "ELAPSED_OR_ENDPOINTS_UNAVAILABLE",
            "includesPausedTime": True}, "hypotheses": hypotheses, "evidence": evidence, "logLookup": logs,
        "cost": cost_section([row for row in costs if row.get("expires_at", 0) > now], bool(cost_next)),
        "limitations": ["Counts concern indexed snapshots, not the full DLQ or numbers of shipments.",
            "Reads are bounded and non-transactional; concurrent changes and missing legacy history can make this report partial.",
            "Recovered means recorded per-attempt domain COMPLETE evidence, not a send acknowledgment or a fresh business check.",
            "Reports never receive/replay/delete messages, fetch payloads, invoke a verifier, or extend retention. Optional log lookup is one explicit sample.",
            "Root cause, original-message presence, distributed traces and a final incident bill are not established by this report."]}
    result["reportHash"] = sha(encode(result))
    return result
