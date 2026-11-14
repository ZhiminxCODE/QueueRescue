import json
import os
import sqlite3
import tempfile
import time
import unittest

from backend.service import DESTINATION, Problem, Store, canonical, digest, mask

OPERATOR = {"name": "Alex Chen", "role": "Operator"}
APPROVER = {"name": "Morgan Lee", "role": "Approver"}
VIEWER = {"name": "Sam Rivera", "role": "Viewer"}


class RecoverySafetyTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(seed_count=100)

    def tearDown(self):
        self.store.db.close()

    def mutation(self, fn):
        with self.store.transaction():
            return fn()

    def make_plan(self, filters=None, rate=25):
        return self.mutation(lambda: self.store.create_plan(OPERATOR, {"filters": filters or {}, "rate": rate}))

    def approve(self, plan):
        return self.mutation(lambda: self.store.approve(APPROVER, plan["id"], {"planHash": plan["hash"], "note": "Reviewed and approved."}))

    def start(self, plan, fault=False):
        return self.mutation(lambda: self.store.start(OPERATOR, {"approvedPlanId": plan["id"], "simulateCrash": fault}))

    def ready_job(self, fault=False, filters=None):
        return self.start(self.approve(self.make_plan(filters)), fault)

    def verify(self, identifier):
        return self.mutation(lambda: self.store.verify(OPERATOR, identifier))

    def drain(self, identifier):
        for _ in range(120):
            if self.store.job(identifier)["state"] != "RUNNING":
                break
            self.store.tick(identifier)

    def test_classification_excludes_unknown_complete_and_invalid(self):
        plan = self.make_plan()
        self.assertEqual(plan["counts"], {"ELIGIBLE": 75, "REVIEW": 8, "BLOCKED": 16, "SKIP": 1})
        self.assertEqual(len(plan["decisions"]), 100)
        self.assertEqual(self.store.read("SELECT count(*) n FROM deliveries")[0]["n"], 0)

    def test_success_is_only_claimed_after_business_verification(self):
        job = self.ready_job()
        self.drain(job["id"])
        sent = self.store.job(job["id"])
        self.assertEqual(sent["state"], "VERIFYING")
        self.assertEqual(sent["counts"]["VERIFIED"], 0)
        self.assertEqual(sent["counts"]["SENT"], 75)
        verified = self.verify(job["id"])
        self.assertEqual(verified["state"], "COMPLETED")
        self.assertEqual(verified["counts"]["VERIFIED"], 75)
        self.assertEqual(self.store.report(job["id"])["verificationCoverage"], 1)
        self.assertEqual(self.store.read("SELECT count(*) n FROM messages WHERE original_retained=1")[0]["n"], 100)

    def test_delivery_preserves_original_bytes_and_adds_replay_identity(self):
        original = self.store.one("messages", "msg-000011")
        job = self.ready_job(filters={"search": original["operation_id"]})
        self.store.tick(job["id"])
        delivery = self.store.read("SELECT * FROM deliveries")[0]
        self.assertEqual(delivery["body"].encode(), original["body"].encode())
        attrs = json.loads(delivery["attributes"])
        for k, v in json.loads(original["attributes"]).items():
            self.assertEqual(attrs[k], v)
        self.assertEqual(attrs["operationId"], original["operation_id"])
        self.assertEqual(attrs["replayJobId"], job["id"])
        self.assertEqual(attrs["replayAttemptId"], delivery["attempt_id"])

    def test_sent_without_business_proof_remains_unverified(self):
        job = self.ready_job(filters={"search": "shipment:ORD-12010"})
        self.store.tick(job["id"])
        self.mutation(lambda: self.store.db.execute("DELETE FROM shipments WHERE operation_id='shipment:ORD-12010'"))
        self.store.tick(job["id"])
        result = self.verify(job["id"])
        self.assertEqual(result["state"], "PARTIAL")
        self.assertEqual(result["counts"]["UNVERIFIED"], 1)
        self.assertEqual(result["counts"]["VERIFIED"], 0)
        self.assertEqual(self.store.read("SELECT count(*) n FROM operation_locks")[0]["n"], 1)

    def test_crash_after_send_is_reconciled_without_resend(self):
        job = self.ready_job(fault=True)
        self.store.tick(job["id"])
        paused = self.store.job(job["id"])
        self.assertEqual(paused["state"], "PAUSED")
        self.assertEqual(paused["counts"]["UNCERTAIN"], 1)
        with self.assertRaises(Problem):
            self.mutation(lambda: self.store.control(OPERATOR, job["id"], "resume", {}))
        self.assertEqual(self.verify(job["id"])["counts"]["VERIFIED"], 1)
        self.mutation(lambda: self.store.control(OPERATOR, job["id"], "resume", {}))
        self.drain(job["id"])
        self.verify(job["id"])
        self.assertEqual(self.store.read("SELECT count(*) n FROM deliveries")[0]["n"], 75)
        self.assertEqual(self.store.read("SELECT count(*) n FROM shipments")[0]["n"], 76)

    def test_unknown_after_uncertain_delivery_is_not_retried(self):
        job = self.ready_job(True)
        self.store.tick(job["id"])
        uncertain = self.store.read("SELECT * FROM attempts WHERE state='UNCERTAIN'")[0]
        self.mutation(lambda: self.store.db.execute("DELETE FROM shipments WHERE operation_id=?", (uncertain["operation_id"],)))
        self.mutation(lambda: self.store.db.execute("UPDATE messages SET outcome='UNKNOWN' WHERE operation_id=?", (uncertain["operation_id"],)))
        self.assertEqual(self.verify(job["id"])["counts"]["REVIEW"], 1)
        self.mutation(lambda: self.store.control(OPERATOR, job["id"], "resume", {}))
        self.drain(job["id"])
        result = self.verify(job["id"])
        self.assertEqual(result["state"], "PARTIAL")
        self.assertEqual(self.store.read("SELECT count(*) n FROM deliveries WHERE operation_id=?", (uncertain["operation_id"],))[0]["n"], 1)

    def test_overlapping_jobs_conflict_atomically(self):
        p1, p2 = self.approve(self.make_plan()), self.approve(self.make_plan())
        self.start(p1)
        with self.assertRaises(Problem) as e:
            self.start(p2)
        self.assertEqual(e.exception.status, 409)
        self.assertEqual(self.store.read("SELECT count(*) n FROM jobs")[0]["n"], 1)

    def test_cancel_stops_new_sends_and_verifies_existing(self):
        job = self.ready_job()
        self.store.tick(job["id"])
        self.mutation(lambda: self.store.control(OPERATOR, job["id"], "cancel", {}))
        self.store.tick(job["id"])
        result = self.verify(job["id"])
        self.assertEqual(result["state"], "CANCELLED")
        self.assertEqual(result["counts"]["CANCELLED"], 74)
        self.assertEqual(result["counts"]["VERIFIED"], 1)
        self.assertEqual(self.store.read("SELECT count(*) n FROM operation_locks")[0]["n"], 0)

    def test_pause_schedules_no_new_messages(self):
        job = self.ready_job()
        self.mutation(lambda: self.store.control(OPERATOR, job["id"], "pause", {}))
        self.store.tick(job["id"])
        self.assertEqual(self.store.read("SELECT count(*) n FROM deliveries")[0]["n"], 0)

    def test_viewer_cannot_create_approve_or_execute(self):
        with self.assertRaises(Problem):
            self.mutation(lambda: self.store.create_plan(VIEWER, {}))
        plan = self.make_plan()
        with self.assertRaises(Problem):
            self.mutation(lambda: self.store.approve(VIEWER, plan["id"], {}))
        with self.assertRaises(Problem):
            self.mutation(lambda: self.store.start(VIEWER, {"approvedPlanId": plan["id"]}))

    def test_separation_of_duties(self):
        plan = self.make_plan()
        actor = {**OPERATOR, "role": "Admin"}
        with self.assertRaises(Problem) as e:
            self.mutation(lambda: self.store.approve(actor, plan["id"], {"planHash": plan["hash"], "note": "Reviewed."}))
        self.assertEqual(e.exception.status, 403)

    def test_approval_hash_must_match(self):
        plan = self.make_plan()
        with self.assertRaises(Problem):
            self.mutation(lambda: self.store.approve(APPROVER, plan["id"], {"planHash": "wrong", "note": "Reviewed."}))

    def test_expired_plan_cannot_be_approved_or_started(self):
        plan = self.make_plan()
        self.mutation(lambda: self.store.db.execute("UPDATE plans SET expires_at=0 WHERE id=?", (plan["id"],)))
        with self.assertRaises(Problem):
            self.approve(plan)
        plan2 = self.approve(self.make_plan())
        self.mutation(lambda: self.store.db.execute("UPDATE plans SET expires_at=0 WHERE id=?", (plan2["id"],)))
        with self.assertRaises(Problem):
            self.start(plan2)

    def test_expired_running_plan_stops_scheduling(self):
        job = self.ready_job()
        self.mutation(lambda: self.store.db.execute("UPDATE plans SET expires_at=0 WHERE id=?", (job["plan_id"],)))
        self.store.tick(job["id"])
        self.assertEqual(self.store.read("SELECT count(*) n FROM deliveries")[0]["n"], 0)
        self.assertEqual(self.verify(job["id"])["state"], "PARTIAL")

    def test_tampered_plan_cannot_execute(self):
        plan = self.approve(self.make_plan())
        doc = json.loads(self.store.one("plans", plan["id"])["document"])
        doc["rate"] = 100
        self.mutation(lambda: self.store.db.execute("UPDATE plans SET document=? WHERE id=?", (canonical(doc), plan["id"])))
        with self.assertRaises(Problem):
            self.start(plan)

    def test_outcome_is_checked_again_before_send(self):
        job = self.ready_job(filters={"search": "shipment:ORD-12010"})
        self.mutation(lambda: self.store.db.execute("INSERT INTO shipments VALUES (?,?,?)", ("shipment:ORD-12010", "external-shipment", time.time())))
        self.store.tick(job["id"])
        self.assertEqual(self.store.job(job["id"])["counts"]["SKIPPED"], 1)
        self.assertEqual(self.store.read("SELECT count(*) n FROM deliveries")[0]["n"], 0)

    def test_snapshot_tampering_blocks_send(self):
        job = self.ready_job(filters={"search": "shipment:ORD-12010"})
        self.mutation(lambda: self.store.db.execute("UPDATE messages SET body='changed' WHERE operation_id='shipment:ORD-12010'"))
        self.store.tick(job["id"])
        self.assertEqual(self.store.job(job["id"])["counts"]["REVIEW"], 1)

    def test_malformed_payload_is_blocked_even_with_valid_flag(self):
        self.mutation(lambda: self.store.db.execute("UPDATE messages SET body=?,checksum=?,valid=1 WHERE operation_id='shipment:ORD-12010'", ("not json", digest("not json"))))
        plan = self.make_plan({"search": "shipment:ORD-12010"})
        self.assertEqual(plan["counts"]["BLOCKED"], 1)
        self.assertNotIn('not json', canonical(self.store.message('msg-000011')))

    def test_duplicate_operation_only_planned_once(self):
        row = self.store.one("messages", "msg-000011")
        self.mutation(lambda: self.store.db.execute("INSERT INTO messages SELECT 'duplicate',operation_id,error,body,checksum,attributes,observed_at,outcome,valid,original_retained FROM messages WHERE id=?", (row["id"],)))
        plan = self.make_plan({"search": row["operation_id"]})
        self.assertEqual(plan["counts"]["ELIGIBLE"], 1)
        self.assertEqual(plan["counts"]["SKIP"], 1)

    def test_repeated_request_returns_same_result(self):
        action = lambda: self.store.create_plan(OPERATOR, {})
        first = self.store.mutate(OPERATOR, "same-request-key", "/replay-plans", {}, action)
        second = self.store.mutate(OPERATOR, "same-request-key", "/replay-plans", {}, action)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.store.read("SELECT count(*) n FROM plans")[0]["n"], 1)
        with self.assertRaises(Problem):
            self.store.mutate(OPERATOR, "same-request-key", "/replay-plans", {"rate": 40}, action)

    def test_repeated_start_returns_one_job(self):
        plan = self.approve(self.make_plan())
        data = {"approvedPlanId": plan["id"]}
        action = lambda: self.store.start(OPERATOR, data)
        a = self.store.mutate(OPERATOR, "same-start-key", "/replay-jobs", data, action)
        b = self.store.mutate(OPERATOR, "same-start-key", "/replay-jobs", data, action)
        self.assertEqual(a["id"], b["id"])
        with self.assertRaises(Problem):
            self.start(plan)

    def test_sensitive_fields_and_values_are_redacted(self):
        value = mask({"customer": {"email": "a@b.com", "address": "street"}, "nested": [{"authorization": "secret"}], "note": "a@b.com", "orderId": "ORD-1"})
        self.assertNotIn("a@b.com", canonical(value))
        self.assertNotIn("street", canonical(value))
        self.assertEqual(value["orderId"], "ORD-1")
        self.assertNotIn("customer@example.com", canonical(self.store.message("msg-000011")))

    def test_audit_cannot_be_updated_or_deleted(self):
        for sql in ["UPDATE audit SET action='tampered'", "DELETE FROM audit"]:
            with self.assertRaises(sqlite3.IntegrityError):
                self.mutation(lambda: self.store.db.execute(sql))

    def test_audit_hash_chain(self):
        self.approve(self.make_plan())
        previous = "0" * 64
        for row in self.store.read("SELECT * FROM audit ORDER BY sequence"):
            values = [row[k] for k in ["at", "actor", "role", "action", "resource", "detail", "previous_hash"]]
            self.assertEqual(row["previous_hash"], previous)
            self.assertEqual(row["hash"], digest(canonical(values)))
            previous = row["hash"]

    def test_unallowlisted_queue_destination_and_fifo_rejected(self):
        for data in [{"filters": {"queue": "other-dlq"}}, {"destination": "other-queue"}, {"destination": "replay.fifo"}, {"rate": 101}, {"rate": True}]:
            with self.assertRaises(Problem):
                self.mutation(lambda: self.store.create_plan(OPERATOR, data))

    def test_missing_verifier_result_never_becomes_eligible(self):
        self.mutation(lambda: self.store.db.execute("UPDATE messages SET outcome='UNKNOWN'"))
        plan = self.make_plan()
        self.assertEqual(plan["counts"]["ELIGIBLE"], 0)

    def test_complete_filter_includes_newly_verified_business_outcomes(self):
        job = self.ready_job(filters={"search": "shipment:ORD-12010"})
        self.store.tick(job["id"])
        rows = self.store.selection({"outcome": "COMPLETE"})
        self.assertIn("shipment:ORD-12010", [r["operation_id"] for r in rows])


class PersistenceTests(unittest.TestCase):
    def test_restart_recovers_leased_attempt_as_uncertain(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.sqlite3")
            store = Store(path, 100)
            with store.transaction():
                plan = store.create_plan(OPERATOR, {"filters": {"search": "shipment:ORD-12010"}})
                store.approve(APPROVER, plan["id"], {"planHash": plan["hash"], "note": "Reviewed."})
                job = store.start(OPERATOR, {"approvedPlanId": plan["id"]})
                store.db.execute("UPDATE attempts SET state='LEASED'")
            store.db.close()
            reopened = Store(path, 100)
            try:
                result = reopened.job(job["id"])
                self.assertEqual(result["state"], "PAUSED")
                self.assertEqual(result["counts"]["UNCERTAIN"], 1)
                with reopened.transaction():
                    result = reopened.verify(OPERATOR, job["id"])
                self.assertEqual(result["counts"]["REVIEW"], 1)
                self.assertEqual(reopened.read("SELECT count(*) n FROM deliveries")[0]["n"], 0)
            finally:
                reopened.db.close()


if __name__ == "__main__":
    unittest.main()
