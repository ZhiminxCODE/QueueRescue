// Each selection owns its response. Cleanup also ignores late failures so an
// old selection cannot replace a newer plan or restore its approval controls.
export function loadPlanDetail(read, id, onState) {
  let active = true;
  onState({ loading: true, plan: null, error: null });
  Promise.resolve().then(() => read('/replay-plans/' + encodeURIComponent(id))).then(plan => {
    if (plan.id !== id || !Array.isArray(plan.decisions)) throw new Error('The selected plan response is incomplete.');
    if (active) onState({ loading: false, plan, error: null });
  }).catch(error => {
    if (active) onState({ loading: false, plan: null, error: error.message || 'Unable to load this plan.' });
  });
  return () => { active = false; };
}
