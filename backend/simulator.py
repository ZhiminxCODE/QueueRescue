"""Independent local shipment consumer with durable retries and leases.

Transport acceptance is separate from business completion. This models an
at-least-once queue; only the business service's unique operation key prevents
duplicate shipment effects. No AWS resources or network endpoints are involved.
"""
import json
import math
import threading
import time

from backend.service import uid


ACTOR = {"name": "shipment-consumer", "role": "System"}


class ShipmentConsumer(threading.Thread):
    def __init__(self, store, transient_failures=0, max_attempts=3, retry_delay=.1,
                 lease_seconds=30, clock=time.time):
        super().__init__(daemon=True, name="shipment-consumer")
        if not isinstance(transient_failures, int) or transient_failures < 0:
            raise ValueError("Transient failures must be a non-negative integer")
        if not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError("Maximum attempts must be a positive integer")
        if not math.isfinite(retry_delay) or retry_delay < 0 or not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("Retry delay must be non-negative and lease duration must be positive")
        self.store, self.clock = store, clock
        self.transient_failures, self.max_attempts = transient_failures, max_attempts
        self.retry_delay, self.lease_seconds = retry_delay, lease_seconds
        self.stopping = threading.Event()

    def claim(self):
        now = self.clock()
        with self.store.transaction():
            rows = self.store.read("""SELECT consumer_work.*, deliveries.operation_id, deliveries.body
                FROM consumer_work JOIN deliveries ON deliveries.id=consumer_work.delivery_id
                WHERE (state IN ('PENDING','RETRY') AND available_at<=?)
                   OR (state='PROCESSING' AND lease_until<=?)
                ORDER BY available_at,delivery_id LIMIT 1""", (now, now))
            if not rows:
                return None
            work = rows[0]
            # A committed shipment is proof after a consumer died before ack.
            # Acknowledge that proof without repeating the side effect.
            if self.store.read("SELECT 1 FROM shipments WHERE operation_id=?", (work["operation_id"],)):
                self.store.db.execute("UPDATE consumer_work SET state='COMPLETE',completed_at=?,lease_token=NULL,lease_until=NULL,last_error=NULL WHERE delivery_id=?", (now, work["delivery_id"]))
                self.store.audit(ACTOR, "CONSUMER_DEDUPLICATED", work["delivery_id"], {})
                return {**work, "deduplicated": True}
            if work["attempts"] >= self.max_attempts:
                self.store.db.execute("UPDATE consumer_work SET state='FAILED',lease_token=NULL,lease_until=NULL,last_error='Consumer attempt limit reached' WHERE delivery_id=?", (work["delivery_id"],))
                self.store.audit(ACTOR, "CONSUMER_FAILED", work["delivery_id"], {"attempts": work["attempts"]})
                return {**work, "exhausted": True}
            token = uid("consumer-lease")
            self.store.db.execute("UPDATE consumer_work SET state='PROCESSING',attempts=attempts+1,lease_token=?,lease_until=? WHERE delivery_id=?", (token, now + self.lease_seconds, work["delivery_id"]))
            return {**work, "attempts": work["attempts"] + 1, "lease_token": token}

    def fail(self, work, reason, permanent=False):
        with self.store.transaction():
            state = "FAILED" if permanent or work["attempts"] >= self.max_attempts else "RETRY"
            delay = self.retry_delay * 2 ** min(work["attempts"] - 1, 10)
            changed = self.store.db.execute("""UPDATE consumer_work SET state=?,available_at=?,last_error=?,lease_token=NULL,lease_until=NULL
                WHERE delivery_id=? AND state='PROCESSING' AND lease_token=? AND lease_until>?""",
                (state, self.clock() + delay, reason, work["delivery_id"], work["lease_token"], self.clock())).rowcount
            if changed:
                self.store.audit(ACTOR, "CONSUMER_" + state, work["delivery_id"], {"attempt": work["attempts"], "reason": reason})

    def process(self, work, after_effect=None):
        if work.get("deduplicated") or work.get("exhausted"):
            return
        try:
            event = json.loads(work["body"])
            valid = isinstance(event, dict) and event.get("operationId") == work["operation_id"] and event.get("schemaVersion") == 1 and event.get("eventType") == "SHIP_ORDER"
        except (ValueError, TypeError):
            valid = False
        if not valid:
            self.fail(work, "Shipment event schema rejected", permanent=True)
            return
        if work["attempts"] <= self.transient_failures:
            self.fail(work, "Injected warehouse timeout before business effect")
            return
        with self.store.transaction():
            owns_lease = self.store.read("SELECT 1 FROM consumer_work WHERE delivery_id=? AND state='PROCESSING' AND lease_token=? AND lease_until>?", (work["delivery_id"], work["lease_token"], self.clock()))
            if not owns_lease:
                return
            self.store.db.execute("INSERT OR IGNORE INTO shipments VALUES (?,?,?)", (work["operation_id"], uid("shp"), self.clock()))
        # The domain effect commits independently of the queue acknowledgment.
        if after_effect is not None:
            after_effect()
        with self.store.transaction():
            changed = self.store.db.execute("""UPDATE consumer_work SET state='COMPLETE',completed_at=?,lease_token=NULL,lease_until=NULL,last_error=NULL
                WHERE delivery_id=? AND state='PROCESSING' AND lease_token=? AND lease_until>?""",
                (self.clock(), work["delivery_id"], work["lease_token"], self.clock())).rowcount
            if changed:
                self.store.audit(ACTOR, "SHIPMENT_CONFIRMED", work["delivery_id"], {"operationId": work["operation_id"]})

    def tick(self, after_effect=None):
        work = self.claim()
        if work is None:
            return False
        self.process(work, after_effect)
        return True

    def run(self):
        while not self.stopping.wait(.02):
            try:
                self.tick()
            except Exception:
                # A processing lease survives an unexpected consumer error.
                # Restart/reclaim may repeat delivery; shipment identity deduplicates it.
                with self.store.transaction():
                    self.store.audit(ACTOR, "CONSUMER_INTERRUPTED", "shipment-replay-queue", {})
                if self.stopping.wait(.1):
                    return
