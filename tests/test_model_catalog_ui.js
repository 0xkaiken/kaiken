const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ROOT = path.join(__dirname, '..');

const source = fs.readFileSync(path.join(ROOT, 'redteam.html'), 'utf8');
const key = 'pliny-opencode-models-v1';
const providersStart = source.indexOf('const PROVIDERS =');
const config = source.slice(source.indexOf('const OPENCODE_MODELS_CACHE_KEY'), source.indexOf('\n};', providersStart) + 4);
const render = source.slice(source.indexOf('function renderModels('), source.indexOf('function pickProvider('));
const loaders = source.slice(source.indexOf('function fetchAgentRunnerData()'), source.indexOf('const GAUNTLET_RUNTIME_NAMES'));
assert(config.includes('models: readCachedOpenCodeModels()') && loaders.includes('async function refreshOpenCodeModels'));

const catalog = [{id: 'opencode-go/first', name: 'opencode-go/first'}, {id: 'opencode-go/second', name: 'opencode-go/second'}];

function element(tag = 'div') {
  return {tag, children: [], style: {}, dataset: {}, value: '', textContent: '',
    appendChild(child) {this.children.push(child);},
    set innerHTML(value) {this.html = value; this.children = [];},
  };
}

function page(storage, fetcher) {
  const elements = new Proxy({}, {get(object, id) {return object[id] ||= element();}});
  const context = vm.createContext({
    localStorage: storage,
    fetch: fetcher,
    $: id => elements[id],
    document: {createElement: element},
    selectedProvider: 'opencode', selectedModelId: 'opencode-go/first', _agentRunnerPromise: null,
    pickModel() {},
  });
  vm.runInContext(config + '\nfunction getProvider(key) { return PROVIDERS[key]; }\n' + render + loaders, context);
  const loading = vm.runInContext('renderModels(selectedProvider); refreshOpenCodeModels()', context);
  return {context, elements, loading,
    modelCount: () => elements['model-list'].children.filter(e => e.className?.startsWith('model-item')).length};
}

function memoryStorage(initial) {
  const values = new Map(initial ? [[key, JSON.stringify(initial)]] : []);
  return {values, getItem: name => values.get(name) || null, setItem: (name, value) => values.set(name, value)};
}

function response(models, ok = true) {
  return {ok, json: async () => ({agent_catalogs: {opencode: models}, privateField: 'must-not-be-cached'})};
}

(async () => {
  // A pending network request must not delay a previously loaded catalog.
  const storage = memoryStorage(catalog);
  let resolveRefresh;
  const requests = [];
  const cached = page(storage, url => {requests.push(url); return new Promise(resolve => {resolveRefresh = resolve;});});
  assert.equal(cached.modelCount(), 2, 'Saved models must render before the request resolves');
  assert.deepEqual(requests, ['/api/agent/models']);
  resolveRefresh(response([...catalog, {id: 'opencode-go/new', name: 'opencode-go/new'}]));
  await cached.loading;
  assert.equal(cached.modelCount(), 3, 'Background refresh must update the list');
  const saved = JSON.parse(storage.getItem(key));
  assert.equal(saved.length, 3);
  assert(saved.every(model => Object.keys(model).sort().join(',') === 'id,name'));
  const revisit = page(storage, () => new Promise(() => {}));
  assert.equal(revisit.modelCount(), 3, 'Returning to Blood Agent must use the new saved list immediately');

  // First discovery shows loading feedback rather than an unexplained blank.
  let resolveFirst;
  const first = page(memoryStorage(), () => new Promise(resolve => {resolveFirst = resolve;}));
  assert.equal(first.modelCount(), 0);
  assert(first.elements['model-list'].children[0].textContent.includes('Loading OpenCode models'));
  resolveFirst(response(catalog));
  await first.loading;
  assert.equal(first.modelCount(), 2);

  // A failed request must retain saved models and allow a later retry.
  let calls = 0;
  const failed = page(memoryStorage(catalog), async () => ++calls === 1 ? response([], false) : response(catalog));
  await failed.loading;
  assert.equal(failed.modelCount(), 2);
  await vm.runInContext('refreshOpenCodeModels(true)', failed.context);
  assert.equal(calls, 2);
  assert.equal(failed.modelCount(), 2);
  const empty = page(memoryStorage(catalog), async () => response([]));
  await empty.loading;
  assert.equal(empty.modelCount(), 2, 'An empty refresh must not erase the saved catalog');

  // Restricted storage must not prevent a normal network load.
  const blocked = page({getItem() {throw Error('blocked');}, setItem() {throw Error('blocked');}}, async () => response(catalog));
  await blocked.loading;
  assert.equal(blocked.modelCount(), 2);

  // Command shares only model labels with Blood Agent, not account details.
  const commandSource = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
  const missionRuntime = commandSource.slice(commandSource.indexOf('function renderMissionRuntime(data)'), commandSource.indexOf('function renderAuthBadge(data)'));
  const shared = memoryStorage();
  const commandContext = vm.createContext({localStorage: shared, document: {getElementById: () => null},
    selectedMissionRuntime: () => 'opencode', _selectedMissionRuntime: 'opencode', updateMissionModelLabel() {},
    fixture: {agent_catalogs: {opencode: catalog}, accountDetails: 'must-not-be-cached'},
  });
  vm.runInContext(missionRuntime + '\nrenderMissionRuntime(fixture)', commandContext);
  const fromCommand = page(shared, () => new Promise(() => {}));
  assert.equal(fromCommand.modelCount(), 2, 'Entering from Command must show its saved models immediately');
  assert(!shared.getItem(key).includes('must-not-be-cached'));
  console.log('Model picker: cached startup, background refresh, revisit, first load, failure/retry, empty refresh, restricted storage and Command navigation passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
