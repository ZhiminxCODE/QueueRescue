from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.service import Problem, Store, canonical

OPERATOR = {'name': 'Operator', 'role': 'Operator'}
APPROVER = {'name': 'Approver', 'role': 'Approver'}


class PlanSummaryTests(unittest.TestCase):
    def test_large_plan_list_excludes_decision_documents(self):
        store = Store(seed_count=10000)
        self.addCleanup(store.db.close)
        with store.transaction():
            plan = store.create_plan(OPERATOR, {})
        with patch.object(store, 'plan', side_effect=AssertionError('list fetched full detail')):
            listing = store.list_plans()
        self.assertEqual(listing[0]['selectedCount'], 10000)
        self.assertEqual(listing[0]['counts'], plan['counts'])
        self.assertLess(len(canonical(listing).encode()), 1000)
        self.assertGreater(len(canonical(plan).encode()), 2000000)

    def test_legacy_backfill_preserves_plans_approvals_and_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'legacy.sqlite3')
            store = Store(path, seed_count=100)
            with store.transaction():
                plan = store.create_plan(OPERATOR, {})
                approved = store.approve(APPROVER, plan['id'], {'planHash': plan['hash'], 'note': 'Reviewed full immutable plan.'})
            audit = store.read('SELECT * FROM audit')
            # This table is the only schema addition; its absence models a
            # database created by the previous application revision.
            store.db.execute('DROP TABLE plan_summaries')
            store.db.close()
            store = Store(path)
            try:
                self.assertEqual(store.plan(plan['id']), approved)
                self.assertEqual(store.read('SELECT * FROM audit'), audit)
                listing = store.list_plans()
                self.assertEqual(listing[0]['state'], 'APPROVED')
                self.assertEqual(listing[0]['counts'], approved['counts'])
            finally:
                store.db.close()
            # Reopening a migrated database must not reparse full documents.
            with patch('backend.service.plan_list_metadata', side_effect=AssertionError('backfill repeated')):
                store = Store(path)
                try:
                    self.assertEqual(store.list_plans(), listing)
                finally:
                    store.db.close()

    def test_presentation_metadata_cannot_make_blocked_plan_approvable(self):
        store = Store(seed_count=100)
        self.addCleanup(store.db.close)
        with store.transaction():
            plan = store.create_plan(OPERATOR, {'filters': {'error': 'InvalidSchema'}})
            row = store.list_plans()[0]
            store.db.execute('UPDATE plan_summaries SET metadata=? WHERE plan_id=?',
                             (canonical({**row, 'counts': {'ELIGIBLE': 1}}), plan['id']))
        with self.assertRaises(Problem) as caught, store.transaction():
            store.approve(APPROVER, plan['id'], {'planHash': plan['hash'], 'note': 'Metadata is not authority.'})
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(store.plan(plan['id'])['state'], 'READY')

    def test_list_limit_and_tied_timestamps_have_stable_order(self):
        store = Store(seed_count=1)
        self.addCleanup(store.db.close)
        identifiers = []
        with store.transaction():
            for _ in range(103):
                identifiers.append(store.create_plan(OPERATOR, {})['id'])
            store.db.execute('UPDATE plans SET created_at=0')
        self.assertEqual([row['id'] for row in store.list_plans()], sorted(identifiers, reverse=True)[:100])
