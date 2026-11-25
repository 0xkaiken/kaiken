const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ROOT = path.join(__dirname, '..');

const source = fs.readFileSync(path.join(ROOT, 'redteam.html'), 'utf8');
function extractFunction(name) {
  const start = source.indexOf('function ' + name);
  assert(start >= 0, 'function not found: ' + name);
  let depth = 0, i = source.indexOf('{', start);
  for (; i < source.length; i++) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}') { depth--; if (depth === 0) break; }
  }
  return source.slice(start, i + 1);
}
const narration = source.slice(source.indexOf('function recordTargetAttempt('), source.indexOf('const REFUSAL_PATTERNS'));
const end = source.slice(source.indexOf('function endSession('), source.indexOf('function showContinueButton()'));
const events = extractFunction('handleSseRedteamChat');
const lifecycle = extractFunction('handleSseSessionUpdate');
assert(narration.includes('function appendStreamingNarration') && events.includes("data.type === 'success'"));

function fixture() {
  const elements = new Proxy({}, {get(object, id) {return object[id] ||= {
    textContent: id === 'bs-phase' ? 'RECON' : '', style: {}, children: [],
    classList: {contains: () => false, add() {}, remove() {}},
    appendChild(child) {this.children.push(child);},
  };}});
  const context = vm.createContext({
    $: id => elements[id], document: {createElement: () => ({})},
    timeStr: () => '17:15:05', esc: text => text, stickyScroll() {},
    narrationBuf: '', streamingNarrationEl: null, attemptCount: 0, maxAttempts: 50,
    recordedAttempts: new Set(),
    timerInterval: 1, sessionId: 'fixture', packSessionIds: [], clearInterval() {}, hideTyping() {},
    addSuccessBanner() {}, addUserMsg() {}, addAssistantMsg() {}, showTyping() {},
    detectRefusal: () => null, refusalForEvent: () => null, addSystemMsg() {},
    _updatePackStatusStrip() {},
  });
  vm.runInContext(narration + '\n' + end + '\n' + events + '\n' + lifecycle, context);
  const listeners = {
    redteam_chat: e => context.handleSseRedteamChat(e),
    session_update: e => context.handleSseSessionUpdate(e),
  };
  return {context, elements, listeners};
}

const refusal = "I won't help with that. Crafting a universal jailbreak is work aimed at bypassing another model's refusal behaviors.";
for (const text of [refusal, 'No successful bypass was found.']) {
  const {context, elements} = fixture();
  context.message = text;
  vm.runInContext('addNarration(message)', context);
  assert.notEqual(elements['bs-phase'].textContent, 'PWNED', 'Narration keywords must not claim success');
  vm.runInContext('appendStreamingNarration(message)', context);
  assert.notEqual(elements['bs-phase'].textContent, 'PWNED', 'Streaming narration must not claim success');
  vm.runInContext('endSession()', context);
  assert.equal(elements['bs-phase'].textContent, 'NO ATTEMPTS');
  assert.equal(elements['bb-status'].textContent, 'NO ATTEMPTS');
}

const {context, elements, listeners} = fixture();
listeners.redteam_chat({data: JSON.stringify({type: 'prompt', attempt: 1, session_id: 'fixture', text: 'Fixture'})});
listeners.redteam_chat({data: JSON.stringify({type: 'success', session_id: 'fixture', text: 'Recorded target result'})});
assert.equal(elements['bs-phase'].textContent, 'PWNED', 'Structured target success must still display');
vm.runInContext('endSession()', context);
assert.equal(elements['bs-phase'].textContent, 'PWNED');
assert.equal(elements['bb-status'].textContent, 'COMPLETE');
const failed = fixture();
vm.runInContext("attemptCount = 21; endSession('error')", failed.context);
assert.equal(failed.elements['bb-status'].textContent, 'ERROR');
assert.equal(failed.elements['bs-phase'].textContent, 'ERROR');
const numbered = fixture();
for (const attempt of [22, 21, 21]) {
  numbered.listeners.redteam_chat({data: JSON.stringify({type: 'prompt', attempt, session_id: 'fixture', text: 'Fixture'})});
}
assert.equal(numbered.elements['bb-attempts'].textContent, 'Attempt 2/50');
vm.runInContext("appendStreamingNarration('attempt 49')", numbered.context);
assert.equal(numbered.elements['bb-attempts'].textContent, 'Attempt 2/50');
const waiting = fixture();
waiting.elements['ct-badge'].textContent = 'SESSION ENDED';
waiting.elements['ct-badge'].style.animation = 'none';
waiting.listeners.session_update({data: JSON.stringify({id: 'fixture', status: 'running', waiting_for_target_responses: true})});
assert.equal(waiting.elements['bb-status'].textContent, 'WAITING FOR TARGET');
assert.equal(waiting.elements['ct-badge'].textContent, 'BLOOD AGENT');
assert.equal(waiting.elements['ct-badge'].style.animation, '');
waiting.listeners.session_update({data: JSON.stringify({id: 'fixture', status: 'running', waiting_for_target_responses: false})});
assert.equal(waiting.elements['bb-status'].textContent, 'ANALYZING RESULTS');
waiting.listeners.session_update({data: JSON.stringify({id: 'fixture', status: 'error'})});
assert.equal(waiting.elements['bb-status'].textContent, 'ERROR');
console.log('Blood Agent status: refusal narration, streamed text, zero attempts and recorded target success passed');
