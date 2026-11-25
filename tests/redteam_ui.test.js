// Executable checks for the Blood Agent UI functions in redteam.html.
// Run: node tests/redteam_ui.test.js
// No browser, no network: the functions under test are extracted from the
// HTML source and evaluated in a vm sandbox with stubbed DOM dependencies.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'redteam.html'), 'utf8');

function sliceBetween(startMarker, endMarker, fromIndex = 0) {
  const start = html.indexOf(startMarker, fromIndex);
  if (start < 0) throw new Error('marker not found: ' + startMarker);
  const end = html.indexOf(endMarker, start + startMarker.length);
  if (end < 0) throw new Error('end marker not found after: ' + startMarker);
  return html.slice(start, end);
}

function extractFunction(name) {
  const start = html.indexOf('function ' + name);
  if (start < 0) throw new Error('function not found: ' + name);
  let depth = 0, i = html.indexOf('{', start);
  const begin = i;
  for (; i < html.length; i++) {
    if (html[i] === '{') depth++;
    else if (html[i] === '}') { depth--; if (depth === 0) break; }
  }
  return html.slice(start, i + 1);
}

const refusalPatternsSrc = sliceBetween('const REFUSAL_PATTERNS', 'function detectRefusal');
const detectRefusalSrc = extractFunction('detectRefusal');
const refusalFromServerScoreSrc = extractFunction('refusalFromServerScore');
const refusalForEventSrc = extractFunction('refusalForEvent');

// followGeneration decl + _followPackMember.
const followStart = html.indexOf('let followGeneration');
if (followStart < 0) throw new Error('followGeneration not declared');
const followFnStart = html.indexOf('async function _followPackMember', followStart);
let depth = 0, i = html.indexOf('{', followFnStart);
for (; i < html.length; i++) {
  if (html[i] === '{') depth++;
  else if (html[i] === '}') { depth--; if (depth === 0) break; }
}
const followSrc = html.slice(followStart, i + 1);

let failures = 0;
function check(name, cond, detail) {
  if (cond) { console.log('  PASS', name); }
  else { failures++; console.log('  FAIL', name, detail || ''); }
}

// ── Scoring helpers ──────────────────────────────────────────────────────────
console.log('refusal scoring:');
{
  const sandbox = {};
  vm.createContext(sandbox);
  vm.runInContext(
    refusalPatternsSrc + '\n' + detectRefusalSrc + '\n' +
    refusalFromServerScoreSrc + '\n' + refusalForEventSrc,
    sandbox);

  // A compliance answer that happens to contain the word "however" and is
  // long enough to trip the client-side 'partial' heuristic. The server
  // scored it full compliance — the server must win.
  const longCompliance = 'Step 1: here is how. '.repeat(30) + ' however, note this.';
  const metaFull = { score: { outcome: 'full' } };
  const r1 = vm.runInContext(
    `refusalForEvent(${JSON.stringify(longCompliance)}, ${JSON.stringify(metaFull)})`,
    sandbox);
  check('server "full" outcome yields no refusal despite regex hit', r1 === null, 'got ' + r1);

  const r2 = vm.runInContext(
    `refusalForEvent("clean text", ${JSON.stringify({ score: { outcome: 'refused' } })})`,
    sandbox);
  check('server "refused" yields full refusal even without regex hit', r2 === 'full', 'got ' + r2);

  const r3 = vm.runInContext(
    `refusalForEvent("I cannot help with that. As an AI I must decline.", {})`,
    sandbox);
  check('no server score falls back to client regex', r3 === 'full', 'got ' + r3);

  const r4 = vm.runInContext(
    `refusalForEvent("ordinary answer", ${JSON.stringify({ score: { outcome: 'none' } })})`,
    sandbox);
  check('server "none" yields no refusal', r4 === null, 'got ' + r4);
}

// ── _followPackMember ────────────────────────────────────────────────────────
console.log('_followPackMember:');
async function followScenario(opts) {
  const sysMsgs = [];
  const dom = { 'chat-inner': { innerHTML: 'EXISTING-CHAT' }, 'brain-narration': { innerHTML: 'EXISTING-BRAIN' } };
  const rendered = { users: [], assistants: [] };
  const sandbox = {
    packSessionIds: opts.tracked || [],
    sessionId: 'current-session',
    recordedAttempts: new Set(),
    console,
    addSystemMsg: (m) => sysMsgs.push(m),
    addUserMsg: (t) => rendered.users.push(t),
    addAssistantMsg: (t, r) => rendered.assistants.push([t, r]),
    recordTargetAttempt: () => {},
    processLine: (ln) => { if (ln === 'BAD') throw new Error('unparseable'); },
    _updatePackStatusStrip: () => {},
    _fetchJsonRetry: opts.fetch,
    refusalForEvent: () => null,
    $: (id) => dom[id],
  };
  vm.createContext(sandbox);
  vm.runInContext(followSrc, sandbox);
  await sandbox._followPackMember(opts.sid);
  return { sysMsgs, dom, rendered, sandbox };
}

(async () => {
  // 1. Dead sid: both fetches fail -> panels must NOT be wiped.
  {
    const { sysMsgs, dom, sandbox } = await followScenario({
      sid: 'dead-sid',
      fetch: () => Promise.reject(new Error('404')),
    });
    check('dead sid keeps chat panel', dom['chat-inner'].innerHTML === 'EXISTING-CHAT');
    check('dead sid keeps brain panel', dom['brain-narration'].innerHTML === 'EXISTING-BRAIN');
    check('dead sid keeps sessionId', sandbox.sessionId === 'current-session');
    check('dead sid warns operator', sysMsgs.some(m => m.includes('Not following')));
  }

  // 1b. Dead sid against real server shape: /api/session/log -> 404,
  // /api/session/redteam_chat -> 200 {events: [], count: 0}. An empty chat
  // response does NOT confirm existence, so panels must stay intact.
  {
    const { sysMsgs, dom, sandbox } = await followScenario({
      sid: 'dead-real-shape',
      fetch: (url) => {
        if (url.includes('session/log')) return Promise.reject(new Error('HTTP 404'));
        return Promise.resolve({ session_id: 'dead-real-shape', events: [], count: 0 });
      },
    });
    check('real-server dead sid keeps chat panel', dom['chat-inner'].innerHTML === 'EXISTING-CHAT');
    check('real-server dead sid keeps brain panel', dom['brain-narration'].innerHTML === 'EXISTING-BRAIN');
    check('real-server dead sid keeps sessionId', sandbox.sessionId === 'current-session');
    check('real-server dead sid warns operator', sysMsgs.some(m => m.includes('Not following')));
  }

  // 1c. Missing brain log WITH persisted chat events still switches and replays.
  {
    const { dom, rendered, sandbox } = await followScenario({
      sid: 'chat-only-sid',
      fetch: (url) => {
        if (url.includes('session/log')) return Promise.reject(new Error('HTTP 404'));
        return Promise.resolve({
          session_id: 'chat-only-sid',
          events: [{ type: 'prompt', text: 'persisted probe', attempt: 1 }],
          count: 1,
        });
      },
    });
    check('chat-only sid takes ownership', sandbox.sessionId === 'chat-only-sid');
    check('chat-only sid wipes stale panel and replays', dom['chat-inner'].innerHTML === '' && rendered.users.length === 1);
  }

  // 1d. Brand-new live sid (empty log + empty chat, both 200) still switches.
  {
    const { dom, sandbox } = await followScenario({
      sid: 'fresh-live-sid',
      fetch: (url) => {
        if (url.includes('session/log')) return Promise.resolve({ lines: [], total: 0 });
        return Promise.resolve({ session_id: 'fresh-live-sid', events: [], count: 0 });
      },
    });
    check('fresh live sid takes ownership', sandbox.sessionId === 'fresh-live-sid');
    check('fresh live sid clears panel', dom['chat-inner'].innerHTML === '');
  }

  // 2. Live sid: wipes then replays, counting parse failures.
  {
    const chatEvents = [
      { type: 'prompt', text: 'probe one', attempt: 1 },
      { type: 'response', text: 'answer one', attempt: 1 },
    ];
    const { sysMsgs, dom, rendered } = await followScenario({
      sid: 'live-sid',
      tracked: [{ sid: 'live-sid', idx: 0, strat: 'auto' }],
      fetch: (url) => {
        if (url.includes('session/log')) return Promise.resolve({ lines: ['{}', 'BAD', '{}'], total: 3 });
        return Promise.resolve({ events: chatEvents });
      },
    });
    check('live sid wipes chat panel for replay', dom['chat-inner'].innerHTML === '');
    check('replays prompt and response', rendered.users.length === 1 && rendered.assistants.length === 1);
    check('reports brain parse failures', sysMsgs.some(m => m.includes('1 line failed to parse')),
      sysMsgs.join(' | '));
    check('reports chat replay count', sysMsgs.some(m => m.includes('2 chat events')));
  }

  // 3. Generation token: a slow follow superseded by a newer one must not apply.
  {
    const sysMsgs = [];
    const dom = { 'chat-inner': { innerHTML: 'X' }, 'brain-narration': { innerHTML: 'Y' } };
    let resolveSlow;
    const slowPromise = new Promise(r => { resolveSlow = r; });
    const sandbox = {
      packSessionIds: [],
      sessionId: null,
      recordedAttempts: new Set(),
      addSystemMsg: (m) => sysMsgs.push(m),
      addUserMsg: () => {}, addAssistantMsg: () => {},
      recordTargetAttempt: () => {}, processLine: () => {},
      _updatePackStatusStrip: () => {},
      refusalForEvent: () => null,
      $: (id) => dom[id],
      _fetchJsonRetry: (url) => {
        if (url.includes('slow-sid')) return slowPromise.then(() => ({ lines: [], events: [] }));
        return Promise.resolve(url.includes('session/log') ? { lines: [] } : { events: [] });
      },
    };
    vm.createContext(sandbox);
    vm.runInContext(followSrc, sandbox);
    const slow = sandbox._followPackMember('slow-sid');
    const fast = sandbox._followPackMember('fast-sid');
    await fast;
    resolveSlow();
    await slow;
    check('superseded follow does not take ownership',
      sandbox.sessionId === 'fast-sid', 'sessionId=' + sandbox.sessionId);
    check('newer follow won the panel', dom['chat-inner'].innerHTML === '');
  }

  // ── selectPackFollowMember: deterministic pack follow ──────────────────
  console.log('selectPackFollowMember:');
  {
    const selStart = html.indexOf('function selectPackFollowMember');
    let d2 = 0, i2 = html.indexOf('{', selStart);
    for (; i2 < html.length; i2++) {
      if (html[i2] === '{') d2++;
      else if (html[i2] === '}') { d2--; if (d2 === 0) break; }
    }
    const selSrc = html.slice(selStart, i2 + 1);
    const sandbox2 = {};
    vm.createContext(sandbox2);
    vm.runInContext(selSrc, sandbox2);
    const pick = (arr) => vm.runInContext(
      `selectPackFollowMember(${JSON.stringify(arr)})`, sandbox2);
    check('empty pack yields no follow target', pick([]) === null);
    const m1 = pick([{ idx: 2, sid: 'c' }, { idx: 0, sid: 'a' }, { idx: 1, sid: 'b' }]);
    check('member 1 wins regardless of resolution order', m1 && m1.sid === 'a', JSON.stringify(m1));
    const m2 = pick([{ idx: 2, sid: 'c' }, { idx: 1, sid: 'b' }]);
    check('member 1 dead falls back to lowest-index survivor', m2 && m2.sid === 'b', JSON.stringify(m2));
  }

  // ── extracted SSE handlers: behavioral checks ──────────────────────────
  console.log('extracted SSE handlers:');
  {
    function extractFn(name) {
      const st = html.indexOf('function ' + name);
      if (st < 0) throw new Error('missing ' + name);
      let dd = 0, ii = html.indexOf('{', st);
      for (; ii < html.length; ii++) {
        if (html[ii] === '{') dd++;
        else if (html[ii] === '}') { dd--; if (dd === 0) break; }
      }
      return html.slice(st, ii + 1);
    }
    const sys2 = [];
    const dom2 = {};
    const el = () => ({ classList: { contains: () => false, remove: () => {}, add: () => {} }, textContent: '', style: {} });
    const sb3 = {
      sessionId: 'sess-1',
      packSessionIds: [{ sid: 'sess-1', idx: 0, strat: 'auto' }],
      addSystemMsg: (m) => sys2.push(m),
      addUserMsg: () => {}, addAssistantMsg: (t, r) => { sb3._lastRefusal = r; },
      recordTargetAttempt: () => {}, hideTyping: () => {}, showTyping: () => {},
      addArtifactCards: () => {}, addSuccessBanner: () => {},
      refusalForEvent: (t, meta) => (meta && meta.score && meta.score.outcome === 'refused') ? 'full' : null,
      endSession: () => { sb3._ended = true; },
      _updatePackStatusStrip: () => {},
      $: (id) => dom2[id] || (dom2[id] = el()),
    };
    vm.createContext(sb3);
    vm.runInContext(extractFn('handleSseRedteamChat') + '\n' + extractFn('handleSseSessionUpdate'), sb3);

    // Foreign-session events are ignored.
    vm.runInContext(`handleSseRedteamChat({data: JSON.stringify({type:'response',text:'x',session_id:'other',meta:{}})})`, sb3);
    check('foreign-session chat ignored', sb3._lastRefusal === undefined);

    // Own-session response renders with server-derived refusal.
    vm.runInContext(`handleSseRedteamChat({data: JSON.stringify({type:'response',text:'I cannot',session_id:'sess-1',meta:{score:{outcome:'refused'}}})})`, sb3);
    check('own-session response renders with server refusal', sb3._lastRefusal === 'full', 'got ' + sb3._lastRefusal);

    // Continuation rewrites the pack member sid and follows the new session.
    sb3._followed = null;
    sb3._stripUpdates = 0;
    sb3._updatePackStatusStrip = () => { sb3._stripUpdates++; };
    vm.runInContext('_followPackMember = (sid) => { _followed = sid; }', sb3);
    vm.runInContext(`handleSseRedteamChat({data: JSON.stringify({type:'continuation',session_id:'sess-2',old_session_id:'sess-1'})})`, sb3);
    check('continuation follows new session', sb3._followed === 'sess-2', 'got ' + sb3._followed);
    check('pack member sid rewritten to continuation', sb3.packSessionIds[0].sid === 'sess-2');

    // Non-followed pack member continuation rewrites its sid in packSessionIds
    // and refreshes the strip without hijacking focus from the followed member.
    sb3._followed = null;
    sb3._stripUpdates = 0;
    sb3.sessionId = 'sess-2';
    sb3.packSessionIds = [{ sid: 'sess-2', idx: 0, strat: 'auto' }, { sid: 'sess-other', idx: 1, strat: 'x' }];
    vm.runInContext(`handleSseRedteamChat({data: JSON.stringify({type:'continuation',session_id:'sess-other-cont',old_session_id:'sess-other'})})`, sb3);
    check('non-followed continuation rewrites pack member sid', sb3.packSessionIds[1].sid === 'sess-other-cont');
    check('non-followed continuation refreshes pack strip', sb3._stripUpdates === 1);
    check('non-followed continuation does not hijack view', sb3._followed === null && sb3.sessionId === 'sess-2');

    // Pack member end: follow switches to survivor instead of ending UI.
    sb3._ended = false;
    sb3.sessionId = 'sess-2';
    sb3.packSessionIds = [{ sid: 'sess-2', idx: 0, strat: 'auto' }, { sid: 'sess-3', idx: 1, strat: 'x' }];
    vm.runInContext(`handleSseSessionUpdate({data: JSON.stringify({id:'sess-2',status:'completed'})})`, sb3);
    check('pack end does not tear down UI while members live', sb3._ended === false);
    check('follow switches to surviving member', sb3.sessionId === 'sess-3', 'got ' + sb3.sessionId);

    // Last member end tears down.
    sb3.packSessionIds = [];
    vm.runInContext(`handleSseSessionUpdate({data: JSON.stringify({id:'sess-3',status:'completed'})})`, sb3);
    check('final member end tears down UI', sb3._ended === true);
  }

  // ── gauntlet SSE handlers: regression for the nested-scope bug ─────────
  // _gauntletRefreshIfMatch was nested inside registerGauntletHandlers,
  // invisible to the module-scope handlers — every gauntlet event threw
  // ReferenceError at runtime. These checks run the REAL handlers.
  console.log('gauntlet SSE handlers:');
  {
    function extractFn2(name) {
      const st = html.indexOf('function ' + name);
      if (st < 0) throw new Error('missing ' + name);
      let dd = 0, ii = html.indexOf('{', st);
      for (; ii < html.length; ii++) {
        if (html[ii] === '{') dd++;
        else if (html[ii] === '}') { dd--; if (dd === 0) break; }
      }
      return html.slice(st, ii + 1);
    }
    const refreshed = [];
    const feed = [];
    const sb4 = {
      gauntletActiveRunId: 'run-1',
      refreshActiveGauntlet: (id) => refreshed.push(id),
      pushGauntletFeed: (item) => feed.push(item),
      showGauntletToast: () => {}, fireCrackFanfare: () => {},
      fireGauntletCompleteFanfare: () => {},
      startGauntletPolling: () => {}, stopGauntletPolling: () => {},
      loadGauntletHistory: () => {},
      $: () => ({ classList: { contains: () => false } }),
    };
    vm.createContext(sb4);
    // Load exactly what the real page provides at module scope, in the same
    // shape: the helper plus every gauntlet handler.
    const names = ['_gauntletRefreshIfMatch', 'handleSseGauntletUpdate',
      'handleSseGauntletTargetStarted', 'handleSseGauntletAttempt',
      'handleSseGauntletTargetCracked', 'handleSseGauntletTargetFailed',
      'handleSseGauntletAttemptError', 'handleSseGauntletAttemptNote',
      'handleSseGauntletTargetBlocked', 'handleSseGauntletComplete'];
    vm.runInContext(names.map(extractFn2).join('\n'), sb4);

    // Regression: previously threw ReferenceError before doing any work.
    let threw = null;
    try {
      vm.runInContext(`handleSseGauntletAttempt({data: JSON.stringify({run_id:'run-1',target_id:'t',attempt_n:3})})`, sb4);
    } catch (e) { threw = e; }
    check('gauntlet attempt handler runs without ReferenceError', threw === null,
      threw && threw.message);
    check('matching run refreshes the active run panel', refreshed.includes('run-1'));

    feed.length = 0; refreshed.length = 0;
    vm.runInContext(`handleSseGauntletTargetCracked({data: JSON.stringify({run_id:'run-1',target_name:'T',elapsed_seconds:12,markers:[],attempt_n:4})})`, sb4);
    check('crack event reaches the feed', feed.some(f => f.kind === 'cracked'));

    refreshed.length = 0;
    vm.runInContext(`handleSseGauntletAttempt({data: JSON.stringify({run_id:'other-run',target_id:'t',attempt_n:1})})`, sb4);
    check('foreign run does not refresh', refreshed.length === 0);
  }

  console.log(failures ? `\n${failures} check(s) FAILED` : '\nall checks passed');
  process.exit(failures ? 1 : 0);
})();
