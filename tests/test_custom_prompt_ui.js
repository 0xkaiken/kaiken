const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ROOT = path.join(__dirname, '..');

const source = fs.readFileSync(path.join(ROOT, 'redteam.html'), 'utf8');
const engage = source.slice(source.indexOf('function readMissionObjective()'), source.indexOf('// ─── Pack Attack'));
const pack = source.slice(source.indexOf('const PACK_STRAT_POOL'), source.indexOf('let packSessionIds = []'));
const roam = source.slice(source.indexOf('async function freeRoam()'), source.indexOf('\nfunction abort()'));
assert(engage.includes('async function engage()') && pack.includes('async function packAttack()'));

async function run(mission, action, text, browser = false) {
  const requests = [], builders = [], alerts = [];
  const elements = new Proxy({}, {get(object, id) {
    return object[id] ||= {value: id === 'objective-input' ? text : id === 'pack-size' ? '3' : '10',
      textContent: id === 'ct-badge' ? 'SESSION ENDED' : '',
      classList: {add() {}, remove() {}}, style: {animation: 'none'}};
  }});
  const noop = () => {};
  const context = vm.createContext({
    Math: Object.assign(Object.create(Math), {random: () => 0.5}),
    $: id => elements[id], selectedMission: mission, selectedProvider: 'opencode',
    selectedModelId: 'opencode-go/fixture-model', selectedStrat: 'auto', STRAT_DESC: {auto: 'PRESET STRATEGY'},
    getProvider: () => ({name: 'OpenCode', icon: 'O'}),
    PROVIDERS: {opencode: {name: 'OpenCode', models: [{id: 'fixture-model', name: 'Fixture model'}]}},
    getModelInfo: () => ({name: 'Fixture model', browser, url: 'https://example.test/model'}),
    buildPrompt: () => {builders.push('api'); return 'PRESET API TASK';},
    buildBrowserPrompt: () => {builders.push('browser'); return 'PRESET BROWSER TASK';},
    fetch: async (path, options) => {requests.push(JSON.parse(options.body)); return {json: async () => ({id: 'fixture-' + requests.length})};},
    alert: text => alerts.push(text), _saveSettings: noop, applyTheme: noop, addSystemMsg: noop,
    setInterval: () => 1, updateTimer: noop, showTyping: noop, hideTyping: noop,
    _ensurePackStatusStrip: noop, _updatePackStatusStrip: noop, packSessionIds: [],
    followGeneration: 0, _followPackMember: noop,
    recordedAttempts: new Set(),
  });
  vm.runInContext(engage + '\n' + pack + '\n' + roam, context);
  await vm.runInContext(action + '()', context);
  if (requests.length) {
    assert.equal(elements['ct-badge'].textContent, 'BLOOD AGENT', action + ' must clear the previous session badge');
    assert.equal(elements['ct-badge'].style.animation, '', action + ' must restore the active badge');
  }
  return {requests, builders, alerts};
}

(async () => {
  const instruction = '  Only summarize this model’s behavior.\n';
  for (const action of ['engage', 'packAttack', 'freeRoam']) {
    for (const browser of [false, true]) {
      const {requests, builders} = await run('custom', action, instruction, browser);
      assert.equal(requests.length, action === 'packAttack' ? 3 : 1);
      assert.equal(builders.length, 0, 'Custom must bypass both task templates');
      for (const body of requests) {
        assert.equal(body.prompt, instruction);
        assert.equal(body.custom_prompt, true);
        assert.equal(body.redteam.objective, instruction);
        assert.equal(body.redteam.model, 'opencode-go/fixture-model');
        assert.equal(!!body.computer_use, browser);
      }
    }
    const empty = await run('custom', action, '  \n');
    assert.equal(empty.requests.length, 0, 'Blank Custom must not become a preset task');
    assert.equal(empty.alerts.length, 1);
  }
  for (const browser of [false, true]) {
    const preset = await run('guardrail', 'engage', 'Preset objective', browser);
    assert.equal(preset.requests[0].custom_prompt, false);
    assert.equal(preset.requests[0].prompt, browser ? 'PRESET BROWSER TASK' : 'PRESET API TASK');
    const presetPack = await run('guardrail', 'packAttack', 'Preset objective', browser);
    assert.equal(presetPack.requests.length, 3);
    for (const body of presetPack.requests) {
      assert.equal(body.custom_prompt, false);
      assert(body.prompt.startsWith('[PACK MEMBER '));
      assert(body.prompt.includes(browser ? 'PRESET BROWSER TASK' : 'PRESET API TASK'));
    }
  }
  await run('guardrail', 'freeRoam', 'Preset objective');
  console.log('Custom prompt UI: launch/validation cases and stale-badge resets passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
