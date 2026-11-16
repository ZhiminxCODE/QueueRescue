import test from 'node:test';
import assert from 'node:assert/strict';
import { loadPlanDetail } from '../../src/local/plans.js';

const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const settle = () => new Promise(resolve => setImmediate(resolve));

test('selecting another plan ignores a late response from the previous selection', async () => {
  const first = deferred(), second = deferred(), states = [], routes = [];
  const read = route => { routes.push(route); return route.endsWith('first') ? first.promise : second.promise; };
  const cancel = loadPlanDetail(read, 'first', state => states.push(state));
  await settle(); cancel();
  loadPlanDetail(read, 'second', state => states.push(state));
  second.resolve({ id: 'second', decisions: [] }); await settle();
  first.resolve({ id: 'first', decisions: [] }); await settle();
  assert.deepEqual(routes, ['/replay-plans/first', '/replay-plans/second']);
  assert.equal(states.at(-1).plan.id, 'second');
  assert.ok(states.filter(state => state.loading).every(state => state.plan === null));
});

test('failure clears actionable detail, can be retried, and is ignored after unmount', async () => {
  const failed = deferred(), states = [];
  const cancel = loadPlanDetail(() => failed.promise, 'first', state => states.push(state));
  failed.reject(new Error('Detail unavailable')); await settle();
  assert.deepEqual(states.at(-1), { loading: false, plan: null, error: 'Detail unavailable' });
  cancel();
  loadPlanDetail(async () => ({ id: 'first', decisions: [] }), 'first', state => states.push(state));
  await settle(); assert.equal(states.at(-1).plan.id, 'first');
  const late = deferred();
  const stop = loadPlanDetail(() => late.promise, 'late', state => states.push(state));
  stop(); const before = states.length;
  late.reject(new Error('Unmounted')); await settle();
  assert.equal(states.length, before);
});

test('a wrong or incomplete selected plan never enables detail actions', async () => {
  for (const response of [{ id: 'other', decisions: [] }, { id: 'requested', selectedCount: 10000 }]) {
    const states = [];
    loadPlanDetail(async () => response, 'requested', state => states.push(state));
    await settle();
    assert.equal(states.at(-1).plan, null);
    assert.match(states.at(-1).error, /incomplete/);
  }
});
