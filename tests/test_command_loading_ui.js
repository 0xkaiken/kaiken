const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ROOT = path.join(__dirname, '..');

const source = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
function section(start, end) {
  const from = source.indexOf(start), to = source.indexOf(end, from);
  assert(from >= 0 && to > from);
  return source.slice(from, to);
}
function element(tag = '') {
  return {tag, value: '', style: {}, children: [],
    set innerHTML(value) { this.children = []; },
    appendChild(child) { this.children.push(child); },
    get options() { return this.children.flatMap(c => c.tag === 'optgroup' ? c.children : [c]); },
  };
}

(async () => {
  const path = String.raw`C:\Çalışma's workshop\attempts\new "note" & proof.txt`;
  const elements = {'session-msg-input': element(), 'session-msg-target': element()};
  const pending = [], lines = [];
  const context = vm.createContext({
    $: id => elements[id], document: {createElement: element},
    currentFile: null, collapsedDirs: new Set(), activeSessions: [],
    fileSortKey: 'name', fileSortDir: 1,
    _recentEndedSessions: [{id: 'recent', prompt: 'Recent task', status: 'completed'}],
    _currentLogSession: null, _streamingTextEl: {},
    formatSize: () => '10B', timeAgo: () => 'now', saveCollapsed() {},
    clearTerminal: () => { lines.length = 0; },
    renderPromptHeader: prompt => lines.push(prompt),
    addTerminalLine: line => lines.push(line), addTerminalLineRaw: line => lines.push(line),
    api: (method, url) => new Promise((resolve, reject) => pending.push({url, resolve, reject})),
    console: {error() {}},
  });
  vm.runInContext(
    section('function escHtml(', 'function fileIcon(') +
    section('function fileExt(', 'function renderTree(') +
    section('function renderTree(', 'async function refreshFiles(') +
    section('function sessionName(', 'const SESSION_KIND_COLORS') +
    section('function _updateMsgInput(', 'async function sendSessionMessage(') +
    section('let _sessionLogRequest', 'function renderPromptHeader('), context);
  context.fileIcon = () => 'file'; context.recencyClass = () => '';
  const html = context.renderTree([{name: 'fixture', path, is_dir: false}]);
  const decode = value => value.replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
  const datasetPath = decode(html.match(/data-path="([^"]*)"/)[1]);
  context.viewFile = selected => assert.equal(selected, path);
  vm.runInContext('(function() {' + html.match(/onclick="([^"]*)"/)[1] + '})', context)
    .call({dataset: {path: datasetPath}});
  const dirHtml = context.renderTree([{name: 'folder', path, is_dir: true, children: []}]);
  context.toggleDir = (el, selected) => assert.equal(selected, path);
  vm.runInContext('(function() {' + dirHtml.match(/onclick="([^"]*)"/)[1] + '})', context)
    .call({dataset: {path: decode(dirHtml.match(/data-path="([^"]*)"/)[1])}});

  // A selected history entry survives polling even outside the recent eight.
  context._currentLogSession = 'older';
  context._updateMsgInput('older');
  context._updateMsgInput();
  assert.equal(elements['session-msg-target'].value, 'older');
  context.activeSessions = [{id: 'active'}];
  context._updateMsgInput();
  assert.equal(elements['session-msg-target'].value, 'older');

  const old = context.loadSessionLog('older');
  const current = context.loadSessionLog('recent');
  assert.equal(pending[1].url, '/api/session/output?id=recent');
  pending[1].resolve({prompt: 'Recent prompt', lines: ['recent output']}); await current;
  pending[0].resolve({prompt: 'Old prompt', lines: ['old output']}); await old;
  assert.deepEqual(lines, ['Recent prompt', 'recent output']);
  const failed = context.loadSessionLog('failure');
  pending[2].reject(new TypeError('Failed to fetch')); await failed;
  assert.deepEqual(lines, ['[ERROR] Failed to load session log: Failed to fetch']);

  const selector = source.match(/<select id="session-msg-target"[^>]*onchange="([^"]*)"/)[1];
  const selections = [];
  context.loadSessionLog = id => selections.push(id);
  const change = vm.runInContext('(function() {' + selector + '})', context);
  change.call({value: 'older'}); change.call({value: '__ALL__'}); change.call({value: ''});
  assert.deepEqual(selections, ['older']);

  context.$terminal = element(); context.$termLineCount = element(); context.termLineCount = 1;
  vm.runInContext(section('function clearTerminal()', 'function toggleAutoscroll()'), context);
  context.clearTerminal();
  assert.equal(context._streamingTextEl, null);
  console.log('Command loading UI: path handling, session selection, stale responses and error details passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
