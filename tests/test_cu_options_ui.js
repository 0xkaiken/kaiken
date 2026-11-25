const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');

// 1. Markup invariant: Computer Use sub-options live inside #computer-use-options[hidden].
const rowStart = source.indexOf('<div class="cu-toggle-row">');
const rowEnd = source.indexOf('</details>', rowStart);
assert(rowStart >= 0 && rowEnd > rowStart, 'cu-toggle-row must exist inside #launch-options');
const rowHtml = source.slice(rowStart, rowEnd);
const optStart = rowHtml.indexOf('<div id="computer-use-options" hidden>');
assert(optStart >= 0, '#computer-use-options must exist and start hidden');
for (const id of ['computer-use-mode', 'desktop-monitor', 'desktop-mode-hint', 'desktop-python-option', 'desktop-python-toggle']) {
  assert(rowHtml.indexOf(`id="${id}"`) > optStart, `#${id} must be inside #computer-use-options`);
}
assert(source.includes('#computer-use-options[hidden]'), 'CSS must guarantee [hidden] hides #computer-use-options');

// Extract the Computer Use Mode section from index.html.
const start = source.indexOf('// ─── Computer Use Mode');
const end = source.indexOf('let _lastCUSession', start);
assert(start >= 0 && end > start, 'Computer Use Mode script block must exist');
const snippet = source.slice(start, end);

function makeHarness(initialStorage = {}, apiImpl = null) {
  const storage = new Map(Object.entries(initialStorage));
  const listeners = { dom: [], pageshow: [], toggle: [], duration: [] };
  const apiCalls = [];
  const toasts = [];
  let kselectClosed = 0;
  let summaryUpdates = 0;

  const elements = {
    'computer-use-toggle': {
      checked: false,
      addEventListener(ev, fn) { if (ev === 'change') listeners.toggle.push(fn); },
    },
    'computer-use-options': { hidden: true },
    'computer-use-mode': { value: 'browser' },
    'desktop-monitor': {
      value: '',
      style: { display: 'none' },
      options: [],
      replaceChildren() { this.options = []; this.value = ''; },
      appendChild(opt) {
        this.options.push(opt);
        if (this.options.length === 1) this.value = opt.value;
      },
    },
    'desktop-mode-hint': { style: { display: 'none' } },
    'desktop-python-option': { hidden: true },
    'desktop-python-toggle': { checked: false },
    'duration-select': {
      addEventListener(ev, fn) { if (ev === 'change') listeners.duration.push(fn); },
    },
    'browser-control-panel': { attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } },
    'cu-control-kind': { textContent: 'Browser' },
  };

  const context = vm.createContext({
    localStorage: {
      getItem: k => (storage.has(k) ? storage.get(k) : null),
      setItem: (k, v) => storage.set(k, String(v)),
    },
    document: {
      getElementById: id => elements[id] || null,
      createElement: () => ({ value: '', textContent: '' }),
      addEventListener(ev, fn) { if (ev === 'DOMContentLoaded') listeners.dom.push(fn); },
    },
    window: {
      addEventListener(ev, fn) { if (ev === 'pageshow') listeners.pageshow.push(fn); },
    },
    KaikenSelect: {
      close() { kselectClosed++; },
    },
    updateLaunchSummary() { summaryUpdates++; },
    showToast(msg, kind) { toasts.push({ msg, kind }); },
    api: async (method, url, body) => {
      apiCalls.push({ method, url, body });
      if (apiImpl) return apiImpl(method, url, body);
      return { monitors: [{ id: 'DISPLAY1', width: 1920, height: 1080, primary: true }] };
    },
  });

  vm.runInContext(snippet, context);

  return {
    context,
    elements,
    storage,
    apiCalls,
    toasts,
    get kselectClosed() { return kselectClosed; },
    get summaryUpdates() { return summaryUpdates; },
    selectedOptions: () => ({ ...context.selectedComputerUseOptions() }),
    fireDomLoaded: () => Promise.all(listeners.dom.map(fn => fn())),
    firePageShow: () => Promise.all(listeners.pageshow.map(fn => fn())),
    setToggle(checked) {
      elements['computer-use-toggle'].checked = checked;
      return Promise.all(listeners.toggle.map(fn => fn()));
    },
  };
}

(async () => {
  // Case 1: Reload with saved 'desktop' mode while Computer Use is OFF.
  {
    const h = makeHarness({ 'pliny-computer-use-mode': 'desktop' });
    await h.fireDomLoaded();
    assert.equal(h.elements['computer-use-options'].hidden, true, 'CU options stay hidden when CU is off on load');
    assert.equal(h.elements['desktop-monitor'].style.display, 'none');
    assert.equal(h.elements['desktop-mode-hint'].style.display, 'none');
    assert.equal(h.elements['desktop-python-option'].hidden, true);
    assert.equal(h.elements['desktop-python-toggle'].checked, false);
    assert.equal(h.apiCalls.length, 0, 'Must not query /api/desktop/status while CU is off');
    assert.equal(h.context.selectedComputerUseMode(), 'browser');
    assert.deepEqual(h.selectedOptions(), {
      cu_mode: 'browser',
      cu_monitor: null,
      cu_desktop_python: false,
    });
  }

  // Case 2: Turn CU ON -> desktop + Desktop Python ON -> turn CU OFF -> turn CU ON again.
  {
    const h = makeHarness();
    await h.fireDomLoaded();
    assert.equal(h.elements['computer-use-options'].hidden, true);

    await h.setToggle(true);
    assert.equal(h.elements['computer-use-options'].hidden, false);
    assert.equal(h.elements['desktop-monitor'].style.display, 'none');
    assert.equal(h.elements['desktop-python-option'].hidden, true);
    assert.equal(h.apiCalls.length, 0);

    h.elements['computer-use-mode'].value = 'desktop';
    await h.context.changeComputerUseMode();
    assert.equal(h.storage.get('pliny-computer-use-mode'), 'desktop');
    assert.equal(h.elements['desktop-monitor'].style.display, 'block');
    assert.equal(h.elements['desktop-mode-hint'].style.display, 'block');
    assert.equal(h.elements['desktop-python-option'].hidden, false);
    assert.equal(h.apiCalls.length, 1);
    assert.equal(h.elements['desktop-monitor'].value, 'DISPLAY1');

    h.elements['desktop-python-toggle'].checked = true;
    assert.deepEqual(h.selectedOptions(), {
      cu_mode: 'desktop',
      cu_monitor: 'DISPLAY1',
      cu_desktop_python: true,
    });

    // Closing / turning off Computer Use hides all options and resets Desktop Python.
    const closesBefore = h.kselectClosed;
    const summariesBefore = h.summaryUpdates;
    await h.setToggle(false);
    assert.equal(h.elements['computer-use-options'].hidden, true);
    assert.equal(h.elements['desktop-monitor'].style.display, 'none');
    assert.equal(h.elements['desktop-mode-hint'].style.display, 'none');
    assert.equal(h.elements['desktop-python-option'].hidden, true);
    assert.equal(h.elements['desktop-python-toggle'].checked, false, 'Desktop Python must reset when CU turns off');
    assert(h.kselectClosed > closesBefore, 'Open KaikenSelect menu must close when CU turns off');
    assert(h.summaryUpdates > summariesBefore, 'Launch summary must update immediately');
    assert.deepEqual(h.selectedOptions(), {
      cu_mode: 'browser',
      cu_monitor: null,
      cu_desktop_python: false,
    });

    // Simulate launchWithComputerUse() flipping toggle.checked temporarily while options stay hidden.
    h.elements['computer-use-toggle'].checked = true;
    assert.deepEqual(h.selectedOptions(), {
      cu_mode: 'browser',
      cu_monitor: null,
      cu_desktop_python: false,
    }, 'One-off CU launch while CU options are closed must not inherit hidden desktop mode');
    h.elements['computer-use-toggle'].checked = false;

    // Turning CU back ON reveals options with Desktop Python still Off.
    await h.setToggle(true);
    assert.equal(h.elements['computer-use-options'].hidden, false);
    assert.equal(h.elements['desktop-python-option'].hidden, false);
    assert.equal(h.elements['desktop-python-toggle'].checked, false, 'Desktop Python must require fresh opt-in');
  }

  // Case 3: Switching mode from desktop to browser resets Desktop Python and clears cu_monitor.
  {
    const h = makeHarness({ 'pliny-computer-use-mode': 'desktop' });
    await h.fireDomLoaded();
    await h.setToggle(true);
    h.elements['desktop-python-toggle'].checked = true;
    h.elements['computer-use-mode'].value = 'browser';
    await h.context.changeComputerUseMode();
    assert.equal(h.elements['desktop-monitor'].style.display, 'none');
    assert.equal(h.elements['desktop-mode-hint'].style.display, 'none');
    assert.equal(h.elements['desktop-python-option'].hidden, true);
    assert.equal(h.elements['desktop-python-toggle'].checked, false);
    assert.deepEqual(h.selectedOptions(), {
      cu_mode: 'browser',
      cu_monitor: null,
      cu_desktop_python: false,
    });
  }

  // Case 4: Stale in-flight /api/desktop/status response or error after turning CU OFF is ignored.
  {
    let rejectStatus;
    const h = makeHarness({}, () => new Promise((_, reject) => { rejectStatus = reject; }));
    await h.fireDomLoaded();
    await h.setToggle(true);
    h.elements['computer-use-mode'].value = 'desktop';
    const pending = h.context.changeComputerUseMode();
    await h.setToggle(false);
    rejectStatus(new Error('desktop unavailable'));
    await pending;
    assert.equal(h.toasts.length, 0, 'Stale desktop status error after closing CU must not toast');
    assert.equal(h.elements['computer-use-options'].hidden, true);
  }

  console.log('Computer Use options lifecycle: 4 cases passed');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
