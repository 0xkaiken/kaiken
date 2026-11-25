// Agent log wiring in index.html: Markdown lines, OpenCode delta stitching,
// Claude stream deltas, and the escape-first guarantee end to end.
// Run: node tests/test_agent_log_markdown_ui.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const script = source.slice(source.indexOf('function addTerminalLineRaw('), source.indexOf('const TOOL_ICONS'));
assert(script.includes('function addMarkdownLine'), 'markdown helpers must sit in the terminal block');
assert(source.includes('<script src="/log-render.js"></script>'));

function makeEl() {
  const classes = new Set();
  return {
    innerHTML: '', textContent: '', className: '',
    classList: { add: c => classes.add(c), has: c => classes.has(c) },
  };
}
const lines = [];
const loaded = [];
const terminal = {
  appendChild(el) { lines.push(el); }, get lastElementChild() { return lines[lines.length - 1]; },
  scrollTop: 0, scrollHeight: 0, set innerHTML(v) { lines.length = 0; },
};
const context = vm.createContext({
  document: { createElement: makeEl, head: { appendChild: el => loaded.push(el.src || el.href) } },
  requestAnimationFrame: fn => fn(),
  $terminal: terminal, $termLineCount: {}, termLineCount: 0, autoScroll: true,
  escHtml: s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
  showToast: () => {}, renderToolUse: () => lines.push({ tool: true, className: 'term-line t-tool' }),
});
context.window = context;
vm.runInContext(fs.readFileSync(path.join(root, 'log-render.js'), 'utf8'), context);
vm.runInContext(script, context);
const feed = obj => vm.runInContext(`addTerminalLine(${JSON.stringify(JSON.stringify(obj))}, "s")`, context);

// Claude: each assistant text block is its own Markdown line.
feed({ type: 'assistant', message: { id: 'm1', content: [{ type: 'text', text: '**yağmur** $x^2$ <img src=x onerror=alert(1)>' }] } });
assert.equal(lines.length, 1);
assert(lines[0].className.includes('t-assistant') && lines[0].classList.has('md'));
assert(lines[0].innerHTML.includes('<strong>yağmur</strong>'));
assert(lines[0].innerHTML.includes('data-tex="x^2"'));
assert(!lines[0].innerHTML.includes('<img'), lines[0].innerHTML);

assert.deepEqual(loaded, ['/vendor/katex/katex.min.css', '/vendor/katex/katex.min.js'], 'KaTeX loads lazily on first math');

// OpenCode: deltas of one part merge into one element and re-render as a whole.
for (const d of ['| a | b |\n', '|---|---|\n', '| ş | ö |']) {
  feed({ type: 'assistant', part_id: 'p1', message: { content: [{ type: 'text', text: d }] } });
}
assert.equal(lines.length, 2, 'three deltas of one part are one log line');
assert(lines[1].innerHTML.includes('<td>ş</td><td>ö</td>'), lines[1].innerHTML);

// A tool call in between ends the part; a new part starts a new line.
feed({ type: 'assistant', message: { content: [{ type: 'tool_use', name: 'Bash', input: {} }] } });
feed({ type: 'assistant', part_id: 'p1', message: { content: [{ type: 'text', text: 'after tool' }] } });
assert.equal(lines.length, 4);
feed({ type: 'assistant', part_id: 'p2', message: { content: [{ type: 'text', text: 'next part' }] } });
assert.equal(lines.length, 5);

// Codex: whole messages without part_id never merge.
feed({ type: 'assistant', message: { content: [{ type: 'text', text: 'one' }] } });
feed({ type: 'assistant', message: { content: [{ type: 'text', text: 'two' }] } });
assert.equal(lines.length, 7);

// Claude stream deltas: an unclosed fence closes as more text arrives.
feed({ type: 'content_block_start', content_block: { type: 'text', text: '```py\nprint(1)' } });
feed({ type: 'content_block_delta', delta: { type: 'text_delta', text: '\n```\nbitti **tamam**' } });
const streamed = lines[lines.length - 1];
assert(streamed.innerHTML.includes('<code>print(1)</code>') && streamed.innerHTML.includes('<strong>tamam</strong>'), streamed.innerHTML);

// Final result renders as Markdown too.
feed({ type: 'result', subtype: 'success', result: '# Özet\n\n- ğ' });
const final = lines[lines.length - 1];
assert(final.className.includes('t-final') && final.innerHTML.includes('<div class="md-h md-h1">Özet</div>'));

assert.equal(loaded.length, 2, 'KaTeX is requested once, not per line');

console.log('Agent log markdown: claude, opencode stitching, codex, stream deltas, result passed');
