"""Failure tests across independently committed transport and business outcomes.

Most schedules use an injected clock. Subprocess tests use SIGKILL exactly at a
durable boundary so graceful shutdown cannot hide a recovery defect.
"""
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from backend.service import Store, Worker
from backend.simulator import ShipmentConsumer


OPERATOR = {"name": "Alex Chen", "role": "Operator"}
APPROVER = {"name": "Morgan Lee", "role": "Approver"}
OPERATION = "shipment:ORD-12010"
ROOT = Path(__file__).resolve().parents[1]


def approved_job(store):
    with store.transaction():
        plan = store.create_plan(OPERATOR, {"filters": {"search": OPERATION}})
        store.approve(APPROVER, plan["id"], {"planHash": plan["hash"], "note": "Reviewed async recovery test."})
        return store.start(OPERATOR, {"approvedPlanId": plan["id"]})


def verify(store, job):
    with store.transaction():
        return store.verify(OPERATOR, job["id"])


class Clock:
    def __init__(self):
        self.now = time.time() + 1

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class AsyncRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(seed_count=100, consumer_delay=0)
        self.clock = Clock()
        self.consumer = ShipmentConsumer(self.store, clock=self.clock)

    def tearDown(self):
        self.store.db.close()

    def send(self):
        job = approved_job(self.store)
        self.store.tick(job["id"])
        self.store.tick(job["id"])
        self.assertEqual(self.store.job(job["id"])["state"], "VERIFYING")
        return job

    def work(self):
        return self.store.read("SELECT * FROM consumer_work")[0]

    def shipment_count(self):
        return self.store.read("SELECT count(*) n FROM shipments WHERE operation_id=?", (OPERATION,))[0]["n"]

    def test_transport_acceptance_is_not_business_completion(self):
        job = self.send()
        self.assertEqual(self.store.job(job["id"])["counts"]["SENT"], 1)
        self.assertEqual(self.shipment_count(), 0)
        self.assertEqual(verify(self.store, job)["counts"]["UNVERIFIED"], 1)
        self.assertEqual(self.store.report(job["id"])["recovered"], 0)
        self.assertTrue(self.consumer.tick())
        self.assertEqual(self.shipment_count(), 1)
        self.assertEqual(verify(self.store, job)["state"], "COMPLETED")

    def test_delivery_waits_for_configured_consumer_delay(self):
        self.store.consumer_delay = 60
        job = self.send()
        self.assertFalse(self.consumer.tick())
        self.assertEqual(verify(self.store, job)["state"], "PARTIAL")
        self.clock.advance(61)
        self.assertTrue(self.consumer.tick())
        self.assertEqual(verify(self.store, job)["state"], "COMPLETED")

    def test_transient_failures_back_off_without_resending_transport(self):
        self.consumer = ShipmentConsumer(self.store, transient_failures=2, retry_delay=10, clock=self.clock)
        job = self.send()
        self.assertTrue(self.consumer.tick())
        self.assertEqual(self.work()["state"], "RETRY")
        self.assertEqual(self.shipment_count(), 0)
        self.assertFalse(self.consumer.tick())
        self.clock.advance(10)
        self.assertTrue(self.consumer.tick())
        self.assertEqual(self.work()["attempts"], 2)
        self.assertFalse(self.consumer.tick())
        self.clock.advance(20)
        self.assertTrue(self.consumer.tick())
        self.assertEqual(self.work()["state"], "COMPLETE")
        self.assertEqual(self.shipment_count(), 1)
        self.assertEqual(self.store.read("SELECT count(*) n FROM deliveries")[0]["n"], 1)
        self.assertEqual(verify(self.store, job)["state"], "COMPLETED")

    def test_consumer_attempt_limit_leaves_recovery_unverified(self):
        self.consumer = ShipmentConsumer(self.store, transient_failures=10, max_attempts=3, retry_delay=0, clock=self.clock)
        job = self.send()
        for _ in range(3):
            self.assertTrue(self.consumer.tick())
        self.assertFalse(self.consumer.tick())
        self.assertEqual(self.work()["state"], "FAILED")
        self.assertEqual(self.work()["attempts"], 3)
        self.assertEqual(self.shipment_count(), 0)
        result = verify(self.store, job)
        self.assertEqual(result["state"], "PARTIAL")
        self.assertEqual(result["counts"]["UNVERIFIED"], 1)
        self.assertEqual(self.store.read("SELECT count(*) n FROM operation_locks")[0]["n"], 1)

    def test_poison_delivery_is_quarantined_without_retries(self):
        job = self.send()
        with self.store.transaction():
            self.store.db.execute("UPDATE deliveries SET body='not-json'")
        self.assertTrue(self.consumer.tick())
        self.assertFalse(self.consumer.tick())
        self.assertEqual(self.work()["state"], "FAILED")
        self.assertEqual(self.work()["attempts"], 1)
        self.assertEqual(self.shipment_count(), 0)
        self.assertEqual(verify(self.store, job)["state"], "PARTIAL")

    def test_queue_redelivery_does_not_create_another_shipment(self):
        job = self.send()
        self.assertTrue(self.consumer.tick())
        shipment = self.store.read("SELECT * FROM shipments WHERE operation_id=?", (OPERATION,))[0]
        # Model an at-least-once queue receiving the same delivery again.
        with self.store.transaction():
            self.store.db.execute("UPDATE consumer_work SET state='PENDING',available_at=?", (self.clock(),))
        self.assertTrue(self.consumer.tick())
        self.assertEqual(self.work()["state"], "COMPLETE")
        self.assertEqual(self.store.read("SELECT * FROM shipments WHERE operation_id=?", (OPERATION,))[0], shipment)
        self.assertEqual(self.shipment_count(), 1)
        self.assertEqual(verify(self.store, job)["state"], "COMPLETED")

    def test_expired_lease_fences_the_old_consumer(self):
        self.send()
        first = self.consumer.claim()
        second = ShipmentConsumer(self.store, clock=self.clock)
        self.assertIsNone(second.claim())
        self.clock.advance(31)
        reclaimed = second.claim()
        self.assertIsNotNone(reclaimed)
        self.assertNotEqual(first["lease_token"], reclaimed["lease_token"])
        self.consumer.process(first)
        self.assertEqual(self.shipment_count(), 0)
        second.process(reclaimed)
        self.assertEqual(self.shipment_count(), 1)
        self.assertEqual(self.work()["state"], "COMPLETE")

    def test_crash_after_business_effect_reconciles_queue_ack(self):
        self.send()

        def interrupted():
            raise RuntimeError("Consumer interrupted before queue acknowledgment")

        with self.assertRaises(RuntimeError):
            self.consumer.tick(after_effect=interrupted)
        self.assertEqual(self.work()["state"], "PROCESSING")
        self.assertEqual(self.shipment_count(), 1)
        self.clock.advance(31)
        self.assertTrue(self.consumer.tick())
        self.assertEqual(self.shipment_count(), 1)
        self.assertEqual(self.work()["state"], "COMPLETE")

    def test_verifier_outage_never_falls_back_to_untrusted_success(self):
        job = self.send()
        self.consumer.tick()

        def unavailable(_operation):
            raise TimeoutError("Synthetic status adapter unavailable")

        self.store.status_adapter = unavailable
        result = verify(self.store, job)
        self.assertEqual(result["state"], "PARTIAL")
        self.assertEqual(result["counts"]["UNVERIFIED"], 1)
        self.assertIn("UNKNOWN", result["attempts"][0]["detail"])
        self.assertEqual(self.store.read("SELECT count(*) n FROM operation_locks")[0]["n"], 1)
        self.store.status_adapter = None
        self.assertEqual(verify(self.store, job)["state"], "COMPLETED")

    def test_verifier_outage_before_send_blocks_delivery(self):
        job = approved_job(self.store)
        self.store.status_adapter = lambda operation: "UNKNOWN"
        self.store.tick(job["id"])
        self.assertEqual(self.store.job(job["id"])["counts"]["REVIEW"], 1)
        self.assertEqual(self.store.read("SELECT count(*) n FROM deliveries")[0]["n"], 0)

    def test_malformed_verifier_response_is_unknown(self):
        for value in [None, {}, [], True, "not-a-status"]:
            self.store.status_adapter = lambda operation: value
            self.assertEqual(self.store.outcome(OPERATION), "UNKNOWN")

    def test_cancellation_does_not_undo_already_accepted_work(self):
        job = self.send()
        with self.store.transaction():
            self.store.control(OPERATOR, job["id"], "cancel", {})
        self.assertTrue(self.consumer.tick())
        result = verify(self.store, job)
        self.assertEqual(result["state"], "CANCELLED")
        self.assertEqual(result["counts"]["VERIFIED"], 1)
        self.assertEqual(self.shipment_count(), 1)

    def test_independent_background_workers_complete_after_a_retry(self):
        self.store.consumer_delay = .05
        job = approved_job(self.store)
        sender = Worker(self.store)
        consumer = ShipmentConsumer(self.store, transient_failures=1, retry_delay=.01)
        sender.start()
        consumer.start()
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if self.store.job(job["id"])["state"] == "VERIFYING" and self.shipment_count():
                    break
                time.sleep(.01)
            self.assertEqual(self.store.job(job["id"])["state"], "VERIFYING")
            self.assertEqual(self.shipment_count(), 1)
            self.assertEqual(self.work()["attempts"], 2)
            self.assertEqual(self.store.job(job["id"])["counts"]["VERIFIED"], 0)
            self.assertEqual(verify(self.store, job)["state"], "COMPLETED")
        finally:
            sender.stopping.set()
            consumer.stopping.set()
            sender.join(timeout=2)
            consumer.join(timeout=2)
        self.assertFalse(sender.is_alive())
        self.assertFalse(consumer.is_alive())


class ProcessCrashTests(unittest.TestCase):
    def run_killed_child(self, script, path):
        result = subprocess.run([sys.executable, "-c", script, str(path)], cwd=ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)

    def finish_reconciled_job(self, store, job):
        self.assertEqual(verify(store, job)["counts"]["VERIFIED"], 1)
        with store.transaction():
            store.control(OPERATOR, job["id"], "resume", {})
        store.tick(job["id"])
        self.assertEqual(verify(store, job)["state"], "COMPLETED")
        self.assertEqual(store.read("SELECT count(*) n FROM deliveries")[0]["n"], 1)
        self.assertEqual(store.read("SELECT count(*) n FROM shipments WHERE operation_id=?", (OPERATION,))[0]["n"], 1)
        self.assertEqual(store.read("SELECT count(*) n FROM messages WHERE original_retained=1")[0]["n"], 100)

    def test_sigkill_after_send_keeps_durable_delivery_and_requires_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sender-crash.sqlite3"
            store = Store(str(path), 100, consumer_delay=0)
            job = approved_job(store)
            store.db.close()
            self.run_killed_child('''
import os, signal, sys
from backend.service import Store
store = Store(sys.argv[1], consumer_delay=0)
job = store.read("SELECT id FROM jobs")[0]
with store.transaction():
    store.control({"name":"Alex Chen","role":"Operator"}, job["id"], "resume", {})
store.tick(job["id"], after_send=lambda: os.kill(os.getpid(), signal.SIGKILL))
''', path)
            reopened = Store(str(path), consumer_delay=0)
            try:
                self.assertEqual(reopened.job(job["id"])["state"], "PAUSED")
                self.assertEqual(reopened.job(job["id"])["counts"]["UNCERTAIN"], 1)
                self.assertEqual(verify(reopened, job)["counts"]["REVIEW"], 1)
                self.assertEqual(reopened.read("SELECT count(*) n FROM operation_locks")[0]["n"], 1)
                reopened.tick(job["id"])
                self.assertEqual(reopened.read("SELECT count(*) n FROM deliveries")[0]["n"], 1)
                self.assertTrue(ShipmentConsumer(reopened).tick())
                self.finish_reconciled_job(reopened, job)
            finally:
                reopened.db.close()

    def test_sigkill_after_shipment_deduplicates_consumer_redelivery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "consumer-crash.sqlite3"
            store = Store(str(path), 100, consumer_delay=0)
            job = approved_job(store)
            store.tick(job["id"])
            store.tick(job["id"])
            store.db.close()
            self.run_killed_child('''
import os, signal, sys
from backend.service import Store
from backend.simulator import ShipmentConsumer
store = Store(sys.argv[1], consumer_delay=0)
ShipmentConsumer(store).tick(after_effect=lambda: os.kill(os.getpid(), signal.SIGKILL))
''', path)
            reopened = Store(str(path), consumer_delay=0)
            try:
                work = reopened.read("SELECT * FROM consumer_work")[0]
                self.assertEqual(work["state"], "PROCESSING")
                consumer = ShipmentConsumer(reopened, clock=lambda: work["lease_until"] + 1)
                self.assertTrue(consumer.tick())
                self.assertEqual(reopened.read("SELECT state FROM consumer_work")[0]["state"], "COMPLETE")
                self.finish_reconciled_job(reopened, job)
            finally:
                reopened.db.close()

    def test_existing_database_migrates_without_replaying_completed_shipments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "migration.sqlite3"
            store = Store(str(path), 100, consumer_delay=0)
            job = approved_job(store)
            store.tick(job["id"])
            ShipmentConsumer(store).tick()
            # Original prototype databases had deliveries/shipments but no queue ledger.
            with store.transaction():
                store.db.execute("DROP TABLE consumer_work")
            store.db.close()
            reopened = Store(str(path), consumer_delay=0)
            try:
                self.assertEqual(reopened.read("SELECT state FROM consumer_work")[0]["state"], "COMPLETE")
                self.assertFalse(ShipmentConsumer(reopened).tick())
                self.assertEqual(reopened.read("SELECT count(*) n FROM shipments WHERE operation_id=?", (OPERATION,))[0]["n"], 1)
            finally:
                reopened.db.close()


if __name__ == "__main__":
    unittest.main()
