const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const script = source.slice(source.indexOf('const BRIEFING = (() => {'), source.indexOf('window.toggleBriefing ='));

async function scenario(reply, ok = true) {
  const elements = new Proxy({}, {get: (items, id) => items[id] ||= {style: {}, textContent: '', disabled: false}});
  const requests = [];
  let finish;
  const pending = new Promise(resolve => { finish = resolve; });
  const context = vm.createContext({
    document: {getElementById: id => elements[id]},
    localStorage: {getItem: () => null},
    fullTime: (iso) => (iso ? String(iso) : ''),
    fetch: async (url, options) => {
      requests.push({url, options});
      if (!options) return {json: async () => ({ok: true, entries: [{type: 'tool', title: 'Shipped useful tool'}], counts_by_type: {tool: 1}})};
      await pending;
      return {ok, json: async () => reply};
    },
  });
  vm.runInContext(script, context);
  await vm.runInContext('BRIEFING.refresh()', context);
  const generating = vm.runInContext('BRIEFING.generateSummary()', context);
  assert(elements['briefing-summary'].textContent.includes('Drafting'));
  assert.equal(elements['briefing-summarize'].disabled, true, 'Prevent duplicate summary requests while loading');
  finish();
  await generating;
  const request = requests[1];
  assert.equal(request.url, '/api/ship-log/summary', 'Summarize must not post an event to the red-team log');
  assert.equal(JSON.parse(request.options.body).entries[0].title, 'Shipped useful tool');
  assert.equal(elements['briefing-summarize'].disabled, false);
  return elements['briefing-summary'].textContent;
}

(async () => {
  assert.equal(await scenario({ok: true, summary: '- Shipped useful tool.'}), '- Shipped useful tool.');
  assert.match(await scenario({ok: false, error: 'Configure a model first'}, false), /Configure a model first/);
  assert.match(await scenario({ok: true, request_permitted: true}), /summary failed/i);
  console.log('Briefing summary: loading, success, model errors and invalid responses passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
