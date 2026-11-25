const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(ROOT, 'redteam.html'), 'utf8');

// The census helper must exist as a pure function and mirror gauntlet.py.
const start = source.indexOf('function gauntletAttemptCensus(');
assert(start !== -1, 'gauntletAttemptCensus must exist in redteam.html');
const end = source.indexOf('\n}', start);
const censusSrc = source.slice(start, end + 2);
const census = vm.runInNewContext(`(${censusSrc})`);
// JSON round-trip: vm objects live in another realm, so compare plain data.
const run = (input) => JSON.parse(JSON.stringify(census(input)));

const attempts = [
  {}, {},
  { error_type: 'attacker_refusal', is_error: true, pre_send_gate: true },
  { error_type: 'attacker_refusal', is_error: true },
  { error_type: 'judge_error', is_error: true },
  { error_type: 'blocked', is_error: true },
  { covert_refusal: true },
];
assert.deepEqual(run(attempts), {
  total: 7, delivered: 4, scored: 3,
  attacker_refusal: 2, upstream_error: 1, judge_error: 1,
});
assert.deepEqual(run([]), {
  total: 0, delivered: 0, scored: 0,
  attacker_refusal: 0, upstream_error: 0, judge_error: 0,
});

// The live target card must report delivered attacks, not raw attempt count.
const card = source.slice(source.indexOf('function renderActiveGauntlet('), source.indexOf('const abortBtn'));
assert(card.includes('gauntletAttemptCensus(attempts)'), 'card must use the census');
assert(card.includes('<span class="lbl">delivered</span>'), 'card must label delivered attacks');
assert(card.includes('attacker refusals'), 'card must surface attacker refusals');
assert(!card.includes('(attempts.length / maxAttempts)'), 'progress must not count refusals as budget');

console.log('gauntlet scoreboard honesty ok');