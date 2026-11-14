"""Durable, local implementation of the QueueRescue recovery protocol.

SQLite transactions stand in for conditional DynamoDB writes. The transport and
business verifier are deliberately simulated; this module never calls AWS.
"""
import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager


QUEUE = "shipment-worker-dlq"
DESTINATION = "shipment-replay-queue"
ROLES = {"Viewer": {"read"}, "Operator": {"read", "operate"},
         "Approver": {"read", "approve"}, "Admin": {"read", "operate", "approve", "admin"}}
TERMINAL = {"COMPLETED", "PARTIAL", "CANCELLED", "FAILED"}


def uid(prefix):
    return prefix + "-" + uuid.uuid4().hex[:12]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Problem(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


def require(actor, capability):
    if capability not in ROLES.get(actor.get("role"), set()):
        raise Problem(403, "This action requires the " + capability + " permission.")


SENSITIVE = re.compile(r"email|phone|address|name|password|secret|token|authorization|card|ssn", re.I)


def mask(value):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if SENSITIVE.search(k) else mask(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[REDACTED EMAIL]", value)
        value = re.sub(r"\b(?:\d[ -]?){13,19}\b", "[REDACTED NUMBER]", value)
    return value


class Store:
    def __init__(self, path=":memory:", seed_count=1000):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
          id TEXT PRIMARY KEY, operation_id TEXT, error TEXT NOT NULL,
          body TEXT NOT NULL, checksum TEXT NOT NULL, attributes TEXT NOT NULL,
          observed_at REAL NOT NULL, outcome TEXT NOT NULL, valid INTEGER NOT NULL,
          original_retained INTEGER NOT NULL DEFAULT 1);
        CREATE INDEX IF NOT EXISTS messages_error ON messages(error, observed_at);
        CREATE INDEX IF NOT EXISTS messages_operation ON messages(operation_id);
        CREATE TABLE IF NOT EXISTS plans (
          id TEXT PRIMARY KEY, document TEXT NOT NULL, hash TEXT NOT NULL,
          state TEXT NOT NULL, created_by TEXT NOT NULL, created_at REAL NOT NULL,
          expires_at REAL NOT NULL, approved_by TEXT, approval_note TEXT);
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, plan_id TEXT UNIQUE NOT NULL REFERENCES plans(id),
          state TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
          fault INTEGER NOT NULL DEFAULT 0, fault_used INTEGER NOT NULL DEFAULT 0,
          reason TEXT);
        CREATE TABLE IF NOT EXISTS attempts (
          id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
          message_id TEXT NOT NULL REFERENCES messages(id), operation_id TEXT NOT NULL,
          state TEXT NOT NULL, lease_token TEXT, lease_until REAL, sent_at REAL,
          verified_at REAL, detail TEXT, UNIQUE(job_id, message_id));
        CREATE TABLE IF NOT EXISTS operation_locks (
          operation_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id));
        CREATE TABLE IF NOT EXISTS deliveries (
          id TEXT PRIMARY KEY, attempt_id TEXT UNIQUE NOT NULL,
          operation_id TEXT NOT NULL, body TEXT NOT NULL, attributes TEXT NOT NULL,
          created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS shipments (
          operation_id TEXT PRIMARY KEY, shipment_id TEXT UNIQUE NOT NULL,
          created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS audit (
          sequence INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, actor TEXT NOT NULL,
          role TEXT NOT NULL, action TEXT NOT NULL, resource TEXT NOT NULL,
          detail TEXT NOT NULL, previous_hash TEXT NOT NULL, hash TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
          BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
          BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
        CREATE TABLE IF NOT EXISTS requests (
          actor TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT NOT NULL,
          result TEXT NOT NULL, PRIMARY KEY(actor,key));
        """)
        if not self.db.execute("SELECT 1 FROM messages LIMIT 1").fetchone():
            self.seed(seed_count)
        self.recover()

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def audit(self, actor, action, resource, detail):
        previous = self.db.execute("SELECT hash FROM audit ORDER BY sequence DESC LIMIT 1").fetchone()
        previous = previous[0] if previous else "0" * 64
        at = time.time()
        values = [at, actor["name"], actor["role"], action, resource, canonical(mask(detail)), previous]
        self.db.execute("INSERT INTO audit(at,actor,role,action,resource,detail,previous_hash,hash) VALUES (?,?,?,?,?,?,?,?)",
                        (*values, digest(canonical(values))))

    def seed(self, count):
        now = time.time()
        with self.transaction():
            for i in range(count):
                fraction = i / count
                error = "WarehouseTimeout" if fraction < .824 else "InvalidSchema" if fraction < .986 else "AlreadyShipped"
                outcome = "COMPLETE" if error == "AlreadyShipped" else "UNKNOWN" if fraction < .05 else "IN_PROGRESS" if fraction < .074 else "NOT_STARTED"
                operation = "shipment:ORD-" + str(12000 + i)
                payload = {"eventId": "evt-" + str(4800 + i), "operationId": operation,
                           "eventType": "SHIP_ORDER", "schemaVersion": 1 if error != "InvalidSchema" else 0,
                           "orderId": "ORD-" + str(12000 + i), "warehouseId": "NY-02",
                           "customer": {"email": "customer@example.com", "address": "42 Example Street"}}
                body = json.dumps(payload, indent=2)
                self.db.execute("INSERT INTO messages(id,operation_id,error,body,checksum,attributes,observed_at,outcome,valid) VALUES (?,?,?,?,?,?,?,?,?)",
                                ("msg-" + str(i + 1).zfill(6), operation, error, body, digest(body),
                                 canonical({"correlationId": "corr-" + str(4800 + i), "schemaVersion": "1"}),
                                 now - (3600 + (i % 420) * 60), outcome, error != "InvalidSchema"))
                if outcome == "COMPLETE":
                    self.db.execute("INSERT INTO shipments VALUES (?,?,?)", (operation, "shp-existing-" + str(i), now - 3600))
            self.audit({"name": "demo-collector", "role": "System"}, "SNAPSHOTS_INDEXED", QUEUE,
                       {"count": count, "originalsRetained": True, "synthetic": True})

    def recover(self):
        with self.transaction():
            active = self.db.execute("SELECT id FROM jobs WHERE state IN ('RUNNING','VERIFYING') OR id IN (SELECT job_id FROM attempts WHERE state='LEASED')").fetchall()
            for row in active:
                self.db.execute("UPDATE attempts SET state='UNCERTAIN',detail='Worker restarted before durable acknowledgment' WHERE job_id=? AND state='LEASED'", (row[0],))
                self.db.execute("UPDATE jobs SET state=CASE WHEN state='CANCELLED' THEN 'CANCELLED' ELSE 'PAUSED' END,reason='Worker restart; reconcile before resuming',updated_at=? WHERE id=?", (time.time(), row[0]))
                self.audit({"name": "recovery-worker", "role": "System"}, "RESTART_RECOVERED", row[0], {})

    def read(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, table, identifier):
        # Table names are hardcoded by callers, never supplied by API clients.
        rows = self.read("SELECT * FROM " + table + " WHERE id=?", (identifier,))
        if not rows:
            raise Problem(404, "Resource not found.")
        return rows[0]

    def mutate(self, actor, key, route, body, action):
        if not isinstance(key, str) or not 8 <= len(key) <= 200:
            raise Problem(400, "A unique Idempotency-Key of 8–200 characters is required.")
        fingerprint = digest(canonical([route, body]))
        with self.transaction():
            existing = self.db.execute("SELECT * FROM requests WHERE actor=? AND key=?", (actor["name"], key)).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise Problem(409, "Idempotency-Key was already used with a different request.")
                return json.loads(existing["result"])
            result = action()
            self.db.execute("INSERT INTO requests VALUES (?,?,?,?)", (actor["name"], key, fingerprint, canonical(result)))
            return result

    def selection(self, filters):
        if not isinstance(filters, dict) or set(filters) - {"queue", "error", "search", "outcome", "hours"}:
            raise Problem(400, "Unsupported filter.")
        if filters.get("queue", QUEUE) != QUEUE:
            raise Problem(403, "Queue is not allowlisted.")
        sql, args = "SELECT * FROM messages WHERE 1=1", []
        for field, column in [("error", "error")]:
            if filters.get(field):
                sql += " AND " + column + "=?"
                args.append(filters[field])
        if filters.get("outcome"):
            sql += " AND (CASE WHEN EXISTS(SELECT 1 FROM shipments WHERE shipments.operation_id=messages.operation_id) THEN 'COMPLETE' ELSE outcome END)=?"
            args.append(filters["outcome"])
        if filters.get("search"):
            sql += " AND (instr(operation_id,?) > 0 OR instr(id,?) > 0)"
            args.extend([str(filters["search"])] * 2)
        if filters.get("hours"):
            try:
                hours = float(filters["hours"])
                if not 0 < hours <= 336:
                    raise ValueError()
            except (ValueError, TypeError):
                raise Problem(400, "Time range must be between 0 and 336 hours.")
            sql += " AND observed_at >= ?"
            args.append(time.time() - hours * 3600)
        return self.read(sql + " ORDER BY observed_at,id", args)

    def classify(self, row):
        if not row["valid"] or not row["operation_id"] or digest(row["body"]) != row["checksum"]:
            return "BLOCKED", "Schema or snapshot integrity check failed"
        try:
            payload = json.loads(row["body"])
            if not isinstance(payload, dict) or payload.get("schemaVersion") != 1 or payload.get("eventType") != "SHIP_ORDER" or payload.get("operationId") != row["operation_id"] or not all(isinstance(payload.get(k), str) and payload[k] for k in ["eventId", "orderId", "warehouseId"]):
                raise ValueError()
        except (ValueError, TypeError):
            return "BLOCKED", "Payload does not satisfy the shipment event schema"
        outcome = self.outcome(row["operation_id"])
        if outcome == "COMPLETE":
            return "SKIP", "Shipment already exists; no new side effect permitted"
        if outcome == "NOT_STARTED":
            return "ELIGIBLE", "Business verifier confirms no shipment; consumer deduplicates operationId"
        return "REVIEW", "Business outcome is " + outcome + "; automatic replay blocked"

    def outcome(self, operation):
        if self.read("SELECT 1 FROM shipments WHERE operation_id=?", (operation,)):
            return "COMPLETE"
        rows = self.read("SELECT outcome FROM messages WHERE operation_id=? LIMIT 1", (operation,))
        return rows[0]["outcome"] if rows else "UNKNOWN"

    def message(self, identifier):
        row = self.one("messages", identifier)
        try:
            payload = mask(json.loads(row.pop("body")))
        except ValueError:
            row.pop("body", None)
            payload = "[MALFORMED PAYLOAD REDACTED]"
        decision, reason = self.classify(self.one("messages", identifier))
        return {**row, "payload": payload, "attributes": mask(json.loads(row["attributes"])),
                "decision": decision, "reason": reason, "outcome": self.outcome(row["operation_id"]),
                "evidence": [{"at": row["observed_at"], "kind": "Lambda failure", "detail": row["error"] + ": worker attempt exhausted"},
                             {"at": row["observed_at"] + 1, "kind": "Snapshot indexed", "detail": "SHA-256 verified. Original DLQ message retained."},
                             {"at": time.time(), "kind": "Business status adapter", "detail": "Outcome: " + self.outcome(row["operation_id"])}]}

    def create_plan(self, actor, data):
        require(actor, "operate")
        filters = data.get("filters", {})
        destination = data.get("destination", DESTINATION)
        rate = data.get("rate", 25)
        if isinstance(rate, bool) or not isinstance(rate, int) or not 1 <= rate <= 100:
            raise Problem(400, "Rate must be an integer between 1 and 100 messages/sec.")
        if destination != DESTINATION or destination.endswith(".fifo"):
            raise Problem(400, "Only the allowlisted standard replay queue is permitted.")
        rows = self.selection(filters)
        if not rows:
            raise Problem(400, "No messages match these filters.")
        seen, decisions = set(), []
        for row in rows:
            decision, reason = self.classify(row)
            if decision == "ELIGIBLE" and row["operation_id"] in seen:
                decision, reason = "SKIP", "Duplicate business operation in selection"
            if decision == "ELIGIBLE":
                seen.add(row["operation_id"])
            decisions.append({"messageId": row["id"], "operationId": row["operation_id"],
                              "checksum": row["checksum"], "decision": decision, "reason": reason})
        now, identifier = time.time(), uid("plan")
        document = {"id": identifier, "version": 1, "filters": filters, "destination": destination,
                    "rate": rate, "createdAt": now, "expiresAt": now + 3600,
                    "consumerVersion": "shipment-simulator-v1", "idempotencyContract": "operationId-unique",
                    "expectedSideEffect": "Create at most one shipment for each eligible order", "decisions": decisions}
        plan_hash = digest(canonical(document))
        self.db.execute("INSERT INTO plans VALUES (?,?,?,?,?,?,?,?,?)",
                        (identifier, canonical(document), plan_hash, "READY", actor["name"], now, now + 3600, None, None))
        self.audit(actor, "PLAN_CREATED", identifier, {"hash": plan_hash, "selected": len(rows), "rate": rate})
        return self.plan(identifier)

    def plan(self, identifier):
        row = self.one("plans", identifier)
        document = json.loads(row.pop("document"))
        counts = {key: sum(d["decision"] == key for d in document["decisions"]) for key in ["ELIGIBLE", "SKIP", "REVIEW", "BLOCKED"]}
        return {**row, **document, "counts": counts, "expired": time.time() >= row["expires_at"]}

    def approve(self, actor, identifier, data):
        require(actor, "approve")
        plan = self.plan(identifier)
        if plan["state"] != "READY" or plan["expired"]:
            raise Problem(409, "Plan is expired or is no longer awaiting approval.")
        if data.get("planHash") != plan["hash"]:
            raise Problem(409, "Plan hash mismatch; review the current immutable plan.")
        if plan["created_by"] == actor["name"]:
            raise Problem(403, "A different person must approve this plan.")
        note = data.get("note", "")
        if not isinstance(note, str) or not 5 <= len(note.strip()) <= 1000:
            raise Problem(400, "Provide an approval note of 5–1000 characters.")
        if not plan["counts"]["ELIGIBLE"]:
            raise Problem(409, "Plan has no eligible messages.")
        self.db.execute("UPDATE plans SET state='APPROVED',approved_by=?,approval_note=? WHERE id=?", (actor["name"], note.strip(), identifier))
        self.audit(actor, "PLAN_APPROVED", identifier, {"hash": plan["hash"], "note": note})
        return self.plan(identifier)

    def start(self, actor, data):
        require(actor, "operate")
        plan = self.plan(str(data.get("approvedPlanId", "")))
        document = json.loads(self.one("plans", plan["id"])["document"])
        if plan["state"] != "APPROVED" or plan["expired"] or digest(canonical(document)) != plan["hash"]:
            raise Problem(409, "Execution requires an unexpired, intact, approved plan.")
        candidates = [d for d in plan["decisions"] if d["decision"] == "ELIGIBLE"]
        for candidate in candidates:
            if self.read("SELECT 1 FROM operation_locks WHERE operation_id=?", (candidate["operationId"],)):
                raise Problem(409, "An operation is reserved by another active replay job.")
        identifier, now = uid("job"), time.time()
        self.db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)", (identifier, plan["id"], "RUNNING", now, now, bool(data.get("simulateCrash")), 0, None))
        for candidate in candidates:
            self.db.execute("INSERT INTO operation_locks VALUES (?,?)", (candidate["operationId"], identifier))
            self.db.execute("INSERT INTO attempts(id,job_id,message_id,operation_id,state) VALUES (?,?,?,?,?)",
                            (uid("attempt"), identifier, candidate["messageId"], candidate["operationId"], "QUEUED"))
        self.db.execute("UPDATE plans SET state='EXECUTING' WHERE id=?", (plan["id"],))
        self.audit(actor, "JOB_STARTED", identifier, {"plan": plan["id"], "eligible": len(candidates), "simulateCrash": bool(data.get("simulateCrash"))})
        return self.job(identifier)

    def job(self, identifier):
        job = self.one("jobs", identifier)
        attempts = self.read("SELECT state,count(*) count FROM attempts WHERE job_id=? GROUP BY state", (identifier,))
        counts = {key: 0 for key in ["QUEUED", "LEASED", "SENT", "VERIFIED", "UNCERTAIN", "REVIEW", "SKIPPED", "CANCELLED", "UNVERIFIED", "FAILED"]}
        counts.update({r["state"]: r["count"] for r in attempts})
        job["counts"], job["total"] = counts, sum(counts.values())
        job["plan"] = self.plan(job["plan_id"])
        job["attempts"] = self.read("SELECT * FROM attempts WHERE job_id=? ORDER BY rowid LIMIT 100", (identifier,))
        return job

    def control(self, actor, identifier, action, data):
        require(actor, "operate")
        job = self.job(identifier)
        state = job["state"]
        if action == "pause":
            if state != "RUNNING":
                raise Problem(409, "Only running jobs can be paused.")
            target = "PAUSED"
        elif action == "resume":
            if state != "PAUSED":
                raise Problem(409, "Only paused jobs can be resumed.")
            if job["counts"]["UNCERTAIN"]:
                raise Problem(409, "Reconcile uncertain deliveries before resuming.")
            target = "RUNNING"
        elif action == "cancel":
            if state in TERMINAL:
                raise Problem(409, "This job has already finished.")
            target = "CANCELLED"
            self.db.execute("UPDATE attempts SET state='CANCELLED',detail='Cancelled before send' WHERE job_id=? AND state='QUEUED'", (identifier,))
            self.db.execute("DELETE FROM operation_locks WHERE job_id=? AND operation_id IN (SELECT operation_id FROM attempts WHERE job_id=? AND state='CANCELLED')", (identifier, identifier))
        else:
            raise Problem(404, "Unknown control action.")
        self.db.execute("UPDATE jobs SET state=?,reason=?,updated_at=? WHERE id=?", (target, str(data.get("reason", action))[:500], time.time(), identifier))
        self.audit(actor, "JOB_" + action.upper(), identifier, {"reason": data.get("reason", action)})
        return self.job(identifier)

    def tick(self, identifier):
        """Schedule at most one message. Caller paces ticks using the approved rate."""
        worker = {"name": "replay-worker", "role": "System"}
        with self.transaction():
            job = self.one("jobs", identifier)
            if job["state"] != "RUNNING":
                return
            plan = self.plan(job["plan_id"])
            if plan["expired"]:
                self.db.execute("UPDATE attempts SET state='REVIEW',detail='Approved execution window expired' WHERE job_id=? AND state='QUEUED'", (identifier,))
            rows = self.read("SELECT * FROM attempts WHERE job_id=? AND state='QUEUED' ORDER BY rowid LIMIT 1", (identifier,))
            if not rows:
                self.db.execute("UPDATE jobs SET state='VERIFYING',updated_at=? WHERE id=?", (time.time(), identifier))
                self.audit(worker, "SEND_PHASE_FINISHED", identifier, {})
                return
            attempt = rows[0]
            message = self.one("messages", attempt["message_id"])
            approved = next(d for d in plan["decisions"] if d["messageId"] == message["id"])
            decision, reason = self.classify(message)
            if decision != "ELIGIBLE" or message["checksum"] != approved["checksum"]:
                state = "SKIPPED" if decision == "SKIP" else "REVIEW"
                if message["checksum"] != approved["checksum"]:
                    state, reason = "REVIEW", "Snapshot checksum differs from the approved plan"
                self.db.execute("UPDATE attempts SET state=?,detail=? WHERE id=?", (state, reason, attempt["id"]))
                self.db.execute("DELETE FROM operation_locks WHERE operation_id=? AND job_id=?", (attempt["operation_id"], identifier))
                self.audit(worker, "PREFLIGHT_EXCLUDED", attempt["id"], {"decision": state, "reason": reason})
                return
            self.db.execute("UPDATE attempts SET state='LEASED',lease_token=?,lease_until=? WHERE id=?", (uid("lease"), time.time() + 30, attempt["id"]))
        # Deliberately separate commits: delivery cannot be atomic with the ledger.
        with self.transaction():
            current = self.one("jobs", identifier)
            if current["state"] != "RUNNING":
                state = "CANCELLED" if current["state"] == "CANCELLED" else "QUEUED"
                self.db.execute("UPDATE attempts SET state=? WHERE id=?", (state, attempt["id"]))
                if state == "CANCELLED":
                    self.db.execute("DELETE FROM operation_locks WHERE operation_id=? AND job_id=?", (attempt["operation_id"], identifier))
                return
            attrs = json.loads(message["attributes"])
            attrs.update({"replayJobId": identifier, "replayAttemptId": attempt["id"], "operationId": attempt["operation_id"]})
            self.db.execute("INSERT INTO deliveries VALUES (?,?,?,?,?,?)", (uid("delivery"), attempt["id"], attempt["operation_id"], message["body"], canonical(attrs), time.time()))
            # The simulated consumer owns the UNIQUE operation identity contract.
            self.db.execute("INSERT OR IGNORE INTO shipments VALUES (?,?,?)", (attempt["operation_id"], uid("shp"), time.time()))
        with self.transaction():
            job = self.one("jobs", identifier)
            if job["fault"] and not job["fault_used"]:
                self.db.execute("UPDATE attempts SET state='UNCERTAIN',detail='Injected crash after send, before acknowledgment' WHERE id=?", (attempt["id"],))
                if job["state"] == "RUNNING":
                    self.db.execute("UPDATE jobs SET state='PAUSED',fault_used=1,reason='Simulated worker interruption: reconcile before resuming' WHERE id=?", (identifier,))
                self.audit(worker, "DELIVERY_UNCERTAIN", attempt["id"], {"jobId": identifier})
            else:
                self.db.execute("UPDATE attempts SET state='SENT',sent_at=?,detail='Delivery acknowledged; business verification pending' WHERE id=?", (time.time(), attempt["id"]))

    def verify(self, actor, identifier):
        require(actor, "operate")
        job = self.job(identifier)
        for attempt in self.read("SELECT * FROM attempts WHERE job_id=? AND state IN ('SENT','UNCERTAIN','UNVERIFIED','REVIEW')", (identifier,)):
            outcome = self.outcome(attempt["operation_id"])
            state = "VERIFIED" if outcome == "COMPLETE" else "UNVERIFIED" if attempt["state"] == "SENT" else "REVIEW"
            self.db.execute("UPDATE attempts SET state=?,verified_at=?,detail=? WHERE id=?", (state, time.time(), "Business adapter outcome: " + outcome, attempt["id"]))
            if state == "VERIFIED":
                self.db.execute("DELETE FROM operation_locks WHERE operation_id=? AND job_id=?", (attempt["operation_id"], identifier))
        job = self.job(identifier)
        if job["state"] == "VERIFYING":
            target = "PARTIAL" if any(job["counts"][s] for s in ["UNCERTAIN", "REVIEW", "UNVERIFIED", "FAILED"]) else "COMPLETED"
            self.db.execute("UPDATE jobs SET state=?,updated_at=? WHERE id=?", (target, time.time(), identifier))
        self.audit(actor, "OUTCOMES_VERIFIED", identifier, {"counts": self.job(identifier)["counts"]})
        return self.job(identifier)

    def summary(self):
        total = self.read("SELECT count(*) n FROM messages")[0]["n"]
        counts = {r["state"]: r["n"] for r in self.read("SELECT state,count(*) n FROM attempts GROUP BY state")}
        return {"total": total, "groups": self.incidents({}), "attemptCounts": counts,
                "activeJobs": self.read("SELECT count(*) n FROM jobs WHERE state NOT IN ('COMPLETED','PARTIAL','FAILED','CANCELLED')")[0]["n"],
                "verified": counts.get("VERIFIED", 0), "originalsRetained": total,
                "shipments": self.read("SELECT count(*) n FROM shipments")[0]["n"],
                "environment": "LOCAL SIMULATION", "region": "us-east-1"}

    def incidents(self, filters):
        groups = {}
        for row in self.selection(filters):
            name = row["error"]
            if name not in groups:
                groups[name] = {"id": name, "error": name, "count": 0, "oldest": row["observed_at"], "latest": row["observed_at"],
                                "sampleId": row["id"], "ELIGIBLE": 0, "SKIP": 0, "REVIEW": 0, "BLOCKED": 0}
            group = groups[name]
            group["count"] += 1
            group["latest"] = max(group["latest"], row["observed_at"])
            group[self.classify(row)[0]] += 1
        return sorted(groups.values(), key=lambda r: -r["count"])

    def report(self, identifier):
        job = self.job(identifier)
        decisions = job["plan"]["counts"]
        return {"jobId": identifier, "state": job["state"], "createdAt": job["created_at"],
                "durationSeconds": round(max(0, job["updated_at"] - job["created_at"]), 2),
                "recovered": job["counts"]["VERIFIED"], "selected": len(job["plan"]["decisions"]),
                "excludedAtPlanning": decisions["BLOCKED"] + decisions["REVIEW"] + decisions["SKIP"],
                "unresolved": sum(job["counts"][k] for k in ["UNVERIFIED", "UNCERTAIN", "REVIEW", "FAILED", "QUEUED", "LEASED", "SENT", "CANCELLED"]),
                "verificationCoverage": round(job["counts"]["VERIFIED"] / max(1, job["total"]), 4),
                "originalsRetained": True,
                "duplicateBusinessEffects": self.read("SELECT COALESCE(sum(n-1),0) n FROM (SELECT count(*) n FROM shipments GROUP BY operation_id HAVING count(*)>1)")[0]["n"],
                "evidenceBasis": "Synthetic shipment adapter and UNIQUE operationId constraint",
                "hypothesis": "Warehouse worker timed out. Correlation is simulated and is not proof of a production root cause.",
                "counts": job["counts"], "planHash": job["plan"]["hash"], "approvedBy": job["plan"]["approved_by"]}


class Worker(threading.Thread):
    def __init__(self, store):
        super().__init__(daemon=True, name="queue-rescue-worker")
        self.store, self.stopping, self.next_send = store, threading.Event(), {}

    def run(self):
        while not self.stopping.wait(.01):
            for job in self.store.read("SELECT id,plan_id FROM jobs WHERE state='RUNNING'"):
                if time.monotonic() < self.next_send.get(job["id"], 0):
                    continue
                try:
                    rate = self.store.plan(job["plan_id"])["rate"]
                    self.store.tick(job["id"])
                    self.next_send[job["id"]] = time.monotonic() + 1 / rate
                except Exception:
                    # Never infer a failed send from a worker exception.
                    with self.store.transaction():
                        self.store.db.execute("UPDATE attempts SET state='UNCERTAIN',detail='Worker error; reconciliation required' WHERE job_id=? AND state='LEASED'", (job["id"],))
                        self.store.db.execute("UPDATE jobs SET state='PAUSED',reason='Worker error; inspect and reconcile' WHERE id=? AND state='RUNNING'", (job["id"],))
                        self.store.audit({"name": "replay-worker", "role": "System"}, "WORKER_PAUSED", job["id"], {})
