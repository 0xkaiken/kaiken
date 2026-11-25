const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..');
const read = name => fs.readFileSync(path.join(ROOT, name), 'utf8');

const shell = fs.readFileSync(path.join(ROOT, 'ui-shell.js'), 'utf8');
const guideIds = new Set([...shell.matchAll(/\{ id: '([a-z-]+)'/g)].map(match => match[1]));
assert(guideIds.size > 30, 'guide should describe the whole app');

for (const page of ['index.html', 'redteam.html', 'arcade.html']) {
  const source = read(page);
  assert(source.includes('href="/ui-shell.css"') && source.includes('src="/ui-shell.js"'), `${page} loads the shared shell`);
  const refs = [...source.matchAll(/data-guide="([a-z-]+)"/g), ...source.matchAll(/UIShell\.open\('([a-z-]+)'\)/g)];
  for (const [, id] of refs) assert(guideIds.has(id), `${page} links to unknown guide entry "${id}"`);
}

const index = read('index.html');
const hints = index.slice(index.indexOf('const TAB_HINTS'), index.indexOf('function switchTab'));
const hintedTabs = new Set([...hints.matchAll(/^\s+([a-z]+): \[/gm)].map(match => match[1]));
const tabs = [...index.matchAll(/class="tab[^"]*" data-tab="([a-z]+)"/g)].map(match => match[1]);
assert.equal(tabs.length, 10);
for (const tab of tabs) assert(hintedTabs.has(tab), `tab "${tab}" has no hint`);
for (const [, id] of hints.matchAll(/, '([a-z-]+)'\],/g)) assert(guideIds.has(id), `hint links to unknown guide entry "${id}"`);

// Number keys must follow the visible tab order, with 0 as the tenth tab.
const tabMap = index.slice(index.indexOf('const tabMap = {'), index.indexOf('if (tabMap[e.key])'));
const keyed = [...tabMap.matchAll(/'([0-9])': '([a-z]+)'/g)];
assert.deepEqual(keyed.map(match => match[2]), tabs);

// Commander must inherit the app engine's model: a fixed model name is rejected by OpenCode.
const commander = index.slice(index.indexOf('async function sendBasiliskChat()'), index.indexOf('// Detect any active basilisk dispatch on load'));
assert(commander.includes("agent: 'basilisk-commander'") && !/\bmodel:/.test(commander));

console.log('ui shell ok');

// A duplicate declaration or stray brace blanks a whole page, and no other test loads the Lair.
const vm = require('node:vm');
for (const page of ['index.html', 'redteam.html', 'arcade.html']) {
  const source = read(page);
  for (const [, script] of source.matchAll(/<script>([\s\S]*?)<\/script>/g)) new vm.Script(script, { filename: page });
}

// Map labels must never overlap, and optional ones give way instead of moving.
const arcade = read('arcade.html');
const placer = arcade.slice(arcade.indexOf('function placeMapLabels'), arcade.indexOf('function flushMapLabels'));
const placeMapLabels = vm.runInNewContext(`(${placer})`);
const stack = [
  { text: 'Machinist', cx: 100, y: 50, priority: 1 },
  { text: 'Crescendo', cx: 102, y: 51, priority: 1 },
  { text: 'War Room', cx: 100, y: 50, priority: 5 },
  { text: 'Hidden', cx: 100, y: 50, priority: 0, optional: true },
  { text: 'Far away', cx: 500, y: 50, priority: 0, optional: true },
];
const rects = placeMapLabels(stack, () => 40, 10, 1);
assert.deepEqual(Array.from(rects, r => r.label.text).sort(), ['Crescendo', 'Far away', 'Machinist', 'War Room']);
assert.equal(rects.find(r => r.label.text === 'War Room').y, 50);
for (const a of rects) for (const b of rects) {
  if (a !== b) assert(!(a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y), `${a.label.text} overlaps ${b.label.text}`);
}

// Presets run without screen control unless the switch is on; missions that need it
// turn it on for that launch only.
assert(/<input type="checkbox" id="computer-use-toggle">/.test(index));
assert(!/toggle\.checked = true;\s*launchCustom\(\);\s*\}/.test(index.slice(index.indexOf('function launchPreset'), index.indexOf('let _presetAgent'))));
const launchWithCu = index.slice(index.indexOf('function launchWithComputerUse()'), index.indexOf('// ─── Tweet Pipeline'));
assert(launchWithCu.includes('toggle.checked = previous;'));

// Computer Use fullscreen must toggle without touching the removed action log.
const fullscreen = index.slice(index.indexOf('function toggleCUFullscreen()'), index.indexOf('// Escape exits fullscreen'));
const panelClasses = new Set();
const panel = { classList: { contains: name => panelClasses.has(name), add: name => panelClasses.add(name), remove: name => panelClasses.delete(name) } };
const toggleFullscreen = vm.runInNewContext(`(${fullscreen})`, { document: { getElementById: id => (id === 'cu-panel' ? panel : null) } });
toggleFullscreen();
assert(panelClasses.has('cu-fullscreen'));
toggleFullscreen();
assert(!panelClasses.has('cu-fullscreen'));

// Inline handlers and declarations stay unique once the dead copies are gone.
for (const name of ['toggleBasiliskChat', 'launchShipMode', 'startCUResize', 'toggleCUActionLog']) {
  assert(!index.includes(`function ${name}(`), `${name} should be gone`);
}

// Browser screenshot polling stays same-origin and Ouroboros inherits the CU mode.
assert(!index.includes("'http://localhost:8787/screenshot'"), 'refreshCUScreenshot should stay same-origin');
assert(index.includes("'/api/ouroboros/launch'"), 'api helper should inject cu_mode for Ouroboros launches');

console.log('page scripts and map labels ok');
