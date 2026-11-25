export const title = 'One pairing code, at most one browser';
export function state(v) {
  const count = v.accepted.length;
  if (count > 1) return 'Two browsers accepted the same code';
  const pending = Object.values(v.pc).filter(p => p === 'finish').length;
  if (count === 1) return pending ? 'One accepted; another cached the code' : 'One browser accepted';
  if (!v.codePresent) return 'Code rejected or expired';
  if (pending === 2) return 'Both requests cached the code';
  if (pending === 1) return 'One request cached the code';
  return 'Code available; no browser accepted';
}
export function step(action, from, to) {
  if (action === 'Done') return 'requests finished';
  if (to.accepted.length > from.accepted.length) return 'browser redeems the code';
  if (action.startsWith('ReadUnprotected')) return 'request reads without the lock';
  return 'request is rejected';
}
