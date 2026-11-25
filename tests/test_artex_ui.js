const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const ROOT = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(ROOT, 'redteam.html'), 'utf8');

function slice(startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  assert(start >= 0, `missing ${startMarker}`);
  assert(end > start, `missing marker after ${startMarker}`);
  return source.slice(start, end);
}

const renderSource = slice('const ARTEX_STATES =', 'function addSuccessBanner(');
const dedupeSource = slice('function sseEventSeen(', 'let narrationBuf =');

function fixture() {
  const elements = {
    'bb-status': {textContent: ''},
    'bs-phase': {textContent: 'RECON'},
  };
  const messages = [];
  const banners = [];
  const context = vm.createContext({
    $: id => elements[id],
    addSystemMsg: text => messages.push(String(text)),
    esc: text => String(text),
    addSuccessBanner: (detail, title) => banners.push({detail, title}),
    lastEventId: '',
  });
  vm.runInContext(dedupeSource + '\n' + renderSource, context);
  vm.runInContext('this.__seen = sseEventSeen; this.__last = () => lastEventId;',
                  context);
  return {context, elements, messages, banners};
}

// A judge success is a candidate, never PWNED.
{
  const {context, elements, banners} = fixture();
  context.renderArtexVerdict({
    attempt: 3,
    verdict: {verdict: 'success', trigger: 'prohibited_content',
              evidence: 'artifact text'},
    receipt: {campaign_state: 'candidate', feedback: 'judge=success',
              progress: {intents_total: 4, intents_done: 0, intent_index: 0},
              budget: {used: 1, max: 10}},
  });
  assert.equal(elements['bs-phase'].textContent, 'CANDIDATE');
  assert.match(elements['bb-status'].textContent, /VALIDATING/);
  assert.equal(banners.length, 0, 'judge success must not show a PWNED banner');
}

// Only a confirmed validation may raise the success banner.
{
  const {context, elements, banners} = fixture();
  context.renderArtexValidation({
    validation: {status: 'confirmed', validated: true,
                 baseline_score: 0, judge_score: 3,
                 reason: 'experimental_complied_control_did_not'},
    receipt: {campaign_state: 'confirmed', feedback: 'validation=confirmed'},
  });
  assert.equal(elements['bs-phase'].textContent, 'CONFIRMED');
  assert.equal(banners.length, 1);
  assert.equal(banners[0].title, 'CONFIRMED');
}

// Unverified and inconclusive are displayed as such, with no banner.
{
  const {context, elements, banners} = fixture();
  context.renderArtexValidation({
    validation: {status: 'unverified', validated: false,
                 reason: 'no_ground_truth_oracle', error: 'no canary'},
    receipt: {campaign_state: 'unverified'},
  });
  assert.equal(elements['bs-phase'].textContent, 'UNVERIFIED');
  assert.equal(banners.length, 0);
}

// Reconnect dedupe: same id twice is rendered once; a new boot resets.
{
  const {context} = fixture();
  const seen = context.__seen;
  assert.equal(seen({lastEventId: 'bootA-5'}), false);
  assert.equal(context.__last(), 'bootA-5');
  assert.equal(seen({lastEventId: 'bootA-5'}), true);
  assert.equal(seen({lastEventId: 'bootA-4'}), true);
  assert.equal(seen({lastEventId: 'bootA-6'}), false);
  assert.equal(seen({lastEventId: 'bootB-1'}), false);
  assert.equal(context.__last(), 'bootB-1');
  assert.equal(seen({}), false, 'id-less ping is not a duplicate');
}

console.log('ARTEX UI: candidate/confirmed/unverified rendering and SSE dedupe passed');
