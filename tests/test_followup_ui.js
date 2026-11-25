const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ROOT = path.join(__dirname, '..');

const source = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
const start = source.indexOf('async function sendSessionMessage()');
const end = source.indexOf('let _currentLogSession', start);
assert(start >= 0 && end > start, 'Follow-up entry point must exist');

(async () => {
  for (const launchMode of ['browser', 'desktop']) {
    const requests = [];
    const elements = {
      'session-msg-input': {value: 'Save the current Word document'},
      'session-msg-target': {value: 'desktop-mission', selectedOptions: [{textContent: 'Word mission'}]},
      'computer-use-toggle': {checked: true},
      'desktop-monitor': {value: 'another-monitor'},
    };
    const context = vm.createContext({
      $: id => elements[id],
      selectedComputerUseMode: () => launchMode,
      api: async (method, path, body) => {requests.push({method, path, body: {...body}}); return {resumed: true};},
      showToast() {}, refreshSessions() {},
    });
    vm.runInContext(source.slice(start, end), context);
    await vm.runInContext('sendSessionMessage()', context);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].path, '/api/session/message');
    assert.equal(requests[0].body.session_id, 'desktop-mission');
    assert.equal(requests[0].body.message, 'Save the current Word document');
    assert(!('cu_mode' in requests[0].body), 'Launch selector must not change a continued mission mode');
    assert(!('cu_monitor' in requests[0].body), 'Launch selector must not change a continued mission monitor');
  }
  console.log('Follow-up mode isolation: 2 cases passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
