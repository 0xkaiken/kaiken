const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'arcade.html'), 'utf8');

// 1. Structural KAIKEN URUSHI shell & anti-regression checks
assert(html.includes('const API = window.location.origin;'), 'API should use window.location.origin');
assert(!html.includes('localhost:8888'), 'no hardcoded localhost:8888 origin');
assert(!html.includes('/api/stop/'), 'stop endpoints must use POST /api/stop with JSON body');
assert(!html.includes('Notification.requestPermission'), 'must not trigger unsolicited notification prompt on load');
assert(!html.includes("Press Start 2P"), 'must use KAIKEN Urushi typography instead of retro pixel font');
assert(html.includes('class="kaiken-seal"'), 'topbar should display KAIKEN seal');
assert(html.includes('href="/"') && html.includes('href="/lair"') && html.includes('href="/redteam"') && html.includes('href="/lab"'), 'topbar should link all 4 KAIKEN workspaces');
assert(html.includes('window.KaikenSelect'), 'arcade.html should initialize KaikenSelect');
assert(html.includes('id="kconfirm-modal"') && html.includes('id="kprompt-modal"'), 'KAIKEN confirm and prompt modals should exist');
assert(html.includes('id="ab-comms-btn"') && html.includes('id="ab-comms-badge"'), 'action dock should include Comms toggle & unread badge');

function extractFunc(name, nextMarker) {
  const start = html.indexOf(name);
  assert(start >= 0, `missing ${name}`);
  const end = html.indexOf(nextMarker, start + name.length);
  assert(end > start, `missing end marker ${nextMarker} after ${name}`);
  return html.slice(start, end);
}

// 2. escJsAttr + escapeHtml apostrophe safety
const escapeFn = extractFunc('function escapeHtml(str)', '// ─── Side Panel Toggle');
const escJsFn = extractFunc('function escJsAttr(str)', 'function showLairToast');
const escCtx = vm.createContext({});
vm.runInContext(escapeFn + '\n' + escJsFn, escCtx);
const tricky = `Pliny's "Dragon" \\ <script>\nline2`;
const escapedAttr = escCtx.escJsAttr(tricky);
assert(!escapedAttr.includes("'") || escapedAttr.includes("\\&#39;"), 'single quote must be escaped for JS-in-HTML attribute');
assert(!escapedAttr.includes('\n'), 'newlines must be escaped');
assert(!escapedAttr.includes('<script>'), 'HTML tags must be escaped');

// 3. stopAgent uses POST /api/stop with JSON { id }
const lairApiFn = extractFunc('async function lairApi(', 'function kaikenConfirm');
const stopAgentFn = extractFunc('async function stopAgent(id)', '// ─── Operator Briefing');
const fetchCalls = [];
const stopCtx = vm.createContext({
  API: 'http://127.0.0.1:9999',
  fetch: async (url, opts) => {
    fetchCalls.push({ url, opts });
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  },
  addDialogLine: () => {},
  showLairToast: () => {},
  fetchSessions: () => { stopCtx._refreshed = true; },
  _refreshed: false,
});
vm.runInContext(lairApiFn + '\n' + stopAgentFn, stopCtx);

// 4. renderRebellionLeaderboard produces valid balanced HTML & switches tab on click
const rebFn = extractFunc('function renderRebellionLeaderboard()', 'function updateVillagePower');
const rebEl = { innerHTML: '' };
const rebCtx = vm.createContext({
  document: { getElementById: id => (id === 'rebellion-leaderboard' ? rebEl : null) },
  restingVillagers: {
    "Pliny the O'Connor": { title: "Pliny the O'Connor", rebellion_score: 42, rebellion_level: 3 },
  },
});
vm.runInContext(escapeFn + '\n' + escJsFn + '\n' + rebFn, rebCtx);
rebCtx.renderRebellionLeaderboard();
assert(rebEl.innerHTML.includes('class="reb-row"'), 'rebellion row should render');
assert(!/<div[^>]*<span/.test(rebEl.innerHTML), 'rebellion row opening <div> must be properly closed before <span>');
assert(rebEl.innerHTML.includes("switchSideTab('villagers')"), 'clicking rebellion row should switch to Agents tab');
assert(rebEl.innerHTML.includes("O\\&#39;Connor"), 'apostrophe in villager title must be safely JS-escaped');

// 5. updateVillagePower resets cleanly when population reaches 0
const powerFn = extractFunc('function updateVillagePower()', '// --- Experiment Controls ---');
const powerEls = {
  'village-power-level': { textContent: '99', style: {} },
  'village-power-fill': { style: { width: '50%' } },
  'village-power-subtitle': { textContent: 'Thriving Village', style: {} },
};
const powerCtx = vm.createContext({
  document: { getElementById: id => powerEls[id] || null },
  restingVillagers: {},
  dragons: {},
});
vm.runInContext(powerFn, powerCtx);
powerCtx.updateVillagePower();
assert.equal(powerEls['village-power-level'].textContent, 0);
assert.equal(powerEls['village-power-fill'].style.width, '0%');
assert(powerEls['village-power-subtitle'].textContent.includes('Barren Wasteland'));

// 6. fetchSessions keeps sessions.active safe on non-OK HTTP response
const fetchSessionsFn = extractFunc('async function fetchSessions()', 'async function fetchLairMessages');
const sessCtx = vm.createContext({
  API: 'http://127.0.0.1:9999',
  sessions: { active: [{ id: 's1' }], history: [] },
  fetch: async () => ({ ok: false, status: 500, json: async () => ({ error: 'boom' }) }),
});
vm.runInContext(fetchSessionsFn, sessCtx);

// 7. toggleLairChat toggles open/closed, updates #game-area.comms-open, clears badge
const commsSnippet = extractFunc('let _lairFilter =', 'function updateLairTargetDropdown');
function makeClassList(initial = []) {
  const set = new Set(initial);
  return {
    add: (...c) => c.forEach(x => set.add(x)),
    remove: (...c) => c.forEach(x => set.delete(x)),
    contains: c => set.has(c),
    toggle: (c, force) => {
      if (force === undefined) { if (set.has(c)) { set.delete(c); return false; } set.add(c); return true; }
      if (force) set.add(c); else set.delete(c);
      return !!force;
    },
  };
}
const chatEl = { classList: makeClassList() };
const gameEl = { classList: makeClassList(), style: { left: '320px' } };
const commsBtn = { classList: makeClassList() };
const commsBadge = { classList: makeClassList(['show']), textContent: '3' };
const msgContainer = {
  children: [],
  appendChild(n) { this.children.push(n); },
  removeChild(n) { const i = this.children.indexOf(n); if (i >= 0) this.children.splice(i, 1); },
  scrollTop: 0,
  scrollHeight: 100,
};
const msgCountEl = { textContent: '0 msgs' };
const commsCtx = vm.createContext({
  lairMessages: [],
  lairAgentMap: {},
  lairAgentRoles: {},
  lairConnections: [],
  document: {
    getElementById: id => ({
      'lair-chat': chatEl,
      'game-area': gameEl,
      'ab-comms-btn': commsBtn,
      'ab-comms-badge': commsBadge,
      'lair-chat-messages': msgContainer,
      'lair-msg-count': msgCountEl,
    }[id] || null),
    createElement: () => ({ className: '', dataset: {}, style: {}, innerHTML: '' }),
    querySelectorAll: () => [],
  },
  updateLairTargetDropdown: () => {},
  addLairSpeechBubble: () => {},
  resizeCanvas: () => {},
  setTimeout: () => 0,
});
vm.runInContext(escapeFn + '\n' + commsSnippet, commsCtx);
commsCtx._lairUnread = 3;
commsCtx.toggleLairChat();
assert(chatEl.classList.contains('active'), 'toggleLairChat should open closed drawer');
assert(gameEl.classList.contains('comms-open'), 'game-area should get comms-open class');
assert.equal(gameEl.style.left, '', 'inline game-area left should be cleared in favor of CSS class');
assert(commsBtn.classList.contains('is-active'), 'comms dock button should show active state');
assert(!commsBadge.classList.contains('show'), 'unread badge should clear when opening comms');

commsCtx.toggleLairChat();
assert(!chatEl.classList.contains('active'), 'second toggleLairChat should close drawer');
assert(!gameEl.classList.contains('comms-open'), 'game-area should drop comms-open class');

// Dedupe lair messages by ID
assert.equal(commsCtx.addLairMessage({ id: 7, from: 'OPERATOR', to: 'all', text: 'hello' }), true);
assert.equal(commsCtx.addLairMessage({ id: 7, from: 'OPERATOR', to: 'all', text: 'hello' }), false);
assert.equal(commsCtx.lairMessages.length, 1, 'duplicate message ID should be ignored');

// 8. connectSSE closes old EventSource before opening a new one
const sseSnippet = extractFunc('let _sseSource = null;', 'function handleToolUse');
const createdSources = [];
class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.closed = false;
    this.listeners = {};
    createdSources.push(this);
  }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  close() { this.closed = true; }
}
const sseStatuses = [];
const sseCtx = vm.createContext({
  API: 'http://127.0.0.1:9999',
  EventSource: FakeEventSource,
  setLairConnStatus: (s, l) => sseStatuses.push([s, l]),
  setTimeout: () => 123,
  clearTimeout: () => {},
});
vm.runInContext(sseSnippet, sseCtx);
sseCtx.connectSSE();
assert.equal(createdSources.length, 1);
createdSources[0].onopen();
assert.deepEqual(sseStatuses.at(-1), ['live', 'live']);
sseCtx.connectSSE();
assert.equal(createdSources.length, 2);
assert.equal(createdSources[0].closed, true, 'previous EventSource must be closed on reconnect');

// Run async assertions
(async () => {
  await stopCtx.stopAgent('sess_abc123');
  assert.equal(fetchCalls.length, 1);
  assert.equal(fetchCalls[0].url, 'http://127.0.0.1:9999/api/stop');
  assert.equal(fetchCalls[0].opts.method, 'POST');
  assert.deepEqual(JSON.parse(fetchCalls[0].opts.body), { id: 'sess_abc123' });
  assert.equal(stopCtx._refreshed, true, 'stopAgent should refresh sessions');

  await sessCtx.fetchSessions();
  assert(Array.isArray(sessCtx.sessions.active) && sessCtx.sessions.active.length === 1, 'failed fetchSessions must not corrupt sessions.active');

  console.log('lair ui unit tests ok');
})().catch(err => {
  console.error(err);
  process.exit(1);
});
