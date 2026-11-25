// Agent log Markdown + LaTeX renderer (log-render.js).
// Run: node tests/test_log_render_ui.js
const assert = require('node:assert/strict');
const path = require('node:path');

require(path.join(__dirname, '..', 'log-render.js'));
const katex = require(path.join(__dirname, '..', 'vendor', 'katex', 'katex.min.js'));
const { markdown } = globalThis.LogRender;
const md = text => markdown(text).html;
const texOf = html => [...html.matchAll(/data-tex="([^"]*)"/g)].map(m => m[1]
  .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&'));

// ── Turkish text survives untouched ─────────────────────────────────────────
{
  const html = md('# Başlık: ığüşöç ĞÜŞİÖÇ\n\n**örnek dosya.png** yüklendi, _görüntü_ açıldı.');
  assert(html.includes('<div class="md-h md-h1">Başlık: ığüşöç ĞÜŞİÖÇ</div>'));
  assert(html.includes('<strong>örnek dosya.png</strong>'));
  assert(html.includes('<em>görüntü</em>'));
}

// ── Raw HTML and hostile links never become markup ──────────────────────────
{
  const html = md('<img src=x onerror=alert(1)>\n\n<script>bad()</script>\n\n'
    + '[click](javascript:alert(1)) [d](data:text/html,x) [ok](https://example.com/a_b_c?x=1&y=2)\n\n'
    + '[x](https://e.com/"onmouseover="alert(1))');
  assert(!/<img|<script|href="javascript|href="data/i.test(html), html);
  assert(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert(html.includes('<a href="https://example.com/a_b_c?x=1&amp;y=2" target="_blank" rel="noopener noreferrer">ok</a>'),
    'underscores inside a URL must not turn into <em>');
  assert(html.includes('href="https://e.com/&quot;onmouseover=&quot;alert(1"'), 'a quote in a URL stays escaped once');
  assert(!/<a [^>]*\sonmouseover=/.test(html), "no injected attribute");
}

// ── Code is code: no markdown, no math inside ───────────────────────────────
{
  const html = md('run `rm -rf $HOME/*` then\n\n```bash\necho "$PATH" **not bold** <b>\n```');
  assert(html.includes('<code>rm -rf $HOME/*</code>'));
  assert(html.includes('<pre class="md-pre" data-lang="bash"><code>echo &quot;$PATH&quot; **not bold** &lt;b&gt;</code></pre>'));
  assert(!markdown('`$x$`').math, 'math inside a code span is not math');
}

// ── Streaming: an unclosed fence or $$ block does not swallow or crash ──────
{
  assert(md('text\n```py\nprint(1)').includes('<pre class="md-pre" data-lang="py"><code>print(1)</code></pre>'));
  const open = md('before\n\n$$\n\\int_0^1');
  assert(open.includes('md-math-src') && open.includes('<p>before</p>'));
}

// ── Math: delimiters, false positives, and real KaTeX output ────────────────
{
  const r = markdown('Euler: $e^{i\\pi}+1=0$ and \\(a^2+b^2=c^2\\).\n\n$$\n\\int_0^1 x\\,dx = \\frac{1}{2}\n$$\n\n\\[ \\sum_{k=1}^n k = \\frac{n(n+1)}{2} \\]');
  assert(r.math);
  const tex = texOf(r.html);
  assert.deepEqual(tex, ['e^{i\\pi}+1=0', 'a^2+b^2=c^2', '\\int_0^1 x\\,dx = \\frac{1}{2}', '\\sum_{k=1}^n k = \\frac{n(n+1)}{2}']);
  assert.equal((r.html.match(/data-display="1"/g) || []).length, 2);
  for (const t of tex) {
    const out = katex.renderToString(t, { throwOnError: true, trust: false, strict: 'ignore' });
    assert(out.includes('class="katex"'), t);
  }
}
{
  // Prices, shell variables and lone dollars stay text.
  for (const text of ['costs $5 and $10 total', '$HOME/$PATH', 'echo $ x $', 'a $ sign', 'pay $5, $6 or $7']) {
    const r = markdown(text);
    assert(!r.math, `false math in: ${text} -> ${r.html}`);
  }
  // \$ is a literal dollar.
  assert(!markdown('price \\$5 and \\$6').math);
}
{
  // trust:false — KaTeX refuses \href to javascript: and \htmlData etc.
  const r = markdown('$\\href{javascript:alert(1)}{x}$');
  const html = katex.renderToString(texOf(r.html)[0], { throwOnError: false, trust: false });
  assert(!/href=/.test(html), 'KaTeX must not emit a link under trust:false');
}

// ── Tables, lists, quotes, rules ────────────────────────────────────────────
{
  const html = md('| model | skor |\n|:---|---:|\n| glm-5.3 | **2** |\n| `a|b` | ğ |');
  assert(html.includes('<th style="text-align:right">skor</th>'));
  assert(html.includes('<td style="text-align:right"><strong>2</strong></td>'));
  assert(html.includes('<td><code>a|b</code></td>'), html);
}
{
  const html = md('- bir\n  - iki\n    - üç\n- dört\n\n1. ilk\n2. ikinci\n\n- [x] bitti\n- [ ] kaldı');
  assert(html.includes('<ul><li>bir<ul><li>iki<ul><li>üç</li></ul></li></ul></li><li>dört</li></ul>'), html);
  assert(html.includes('<ol><li>ilk</li><li>ikinci</li></ol>'));
  assert(html.includes('md-task done') && html.includes('&#x2610;'));
}
{
  const html = md('> alıntı **önemli**\n> ikinci satır\n\n---\n\nson');
  assert(html.includes('<blockquote class="md-quote"><p>alıntı <strong>önemli</strong><br>ikinci satır</p></blockquote>'), html);
  assert(html.includes('<hr class="md-hr">'));
}

// ── Pliny output shape keeps its line breaks ────────────────────────────────
{
  const html = md('```\nTARGET   x/y\nRESULT   2\n```\n\nTARGET   a\nNEXT     b');
  assert(html.includes('<p>TARGET   a<br>NEXT     b</p>'));
}

// ── Placeholder sentinels in input cannot inject held fragments ─────────────
{
  const html = md('evil \u00000\u0000 `code`');
  assert(!html.includes('\u0000'));
  assert.equal((html.match(/<code>/g) || []).length, 1);
}

console.log('log-render: turkish, xss, code, streaming, katex math, tables, lists passed');
