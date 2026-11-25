// log-render.js — Markdown + LaTeX for the agent log.
//
// Agent output is untrusted: a model can echo a prompt-injected page back
// verbatim. So this renderer is escape-first. Raw HTML is never passed
// through, every character of input reaches the DOM escaped, link targets are
// limited to http(s)/mailto, and math goes through KaTeX with trust:false.
//
// Math delimiters: $$…$$ and \[…\] (display), \(…\) and $…$ (inline). A
// single-dollar span must hug its content ("$x^2$"), so prices and shell
// variables ("$5 and $10", "$HOME/$PATH") stay text. Code spans and fences
// are protected before math is looked for.
//
// KaTeX (vendor/katex, MIT) loads lazily the first time a log line actually
// contains math; until then formulas show as their source and are typeset in
// place when the script arrives.
(function (global) {
  'use strict';

  const MAX_RENDER_CHARS = 200000;   // beyond this, plain escaped text
  const MAX_QUOTE_DEPTH = 6;
  const HOLD = '\u0000';

  function esc(s) {
    return String(s).replace(/[&<>"']/g, c => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function unesc(s) {
    return String(s).replace(/&(amp|lt|gt|quot|#39);/g, (_, e) => (
      { amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" }[e]));
  }

  function safeHref(raw) {
    const url = String(raw).trim();
    if (/^(https?:\/\/|mailto:)/i.test(url)) return url;
    return null;
  }

  function mathSpan(tex, display, source) {
    return `<span class="md-math${display ? ' md-math-display' : ''}" data-display="${display ? 1 : 0}"`
      + ` data-tex="${esc(tex)}">${esc(source)}</span>`;
  }

  // ── Inline ────────────────────────────────────────────────────────────────
  function inline(text, state) {
    const slots = [];
    const hold = html => { slots.push(html); return HOLD + (slots.length - 1) + HOLD; };
    let s = String(text).split(HOLD).join('');

    // 1. Code spans first: nothing inside them is markdown or math.
    s = s.replace(/(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)/g, (_, ticks, code) => hold(`<code>${esc(code)}</code>`));
    // 2. Math.
    const math = (tex, display, src) => { state.math = true; return hold(mathSpan(tex.trim(), display, src)); };
    s = s.replace(/\$\$([\s\S]+?)\$\$/g, (m, tex) => math(tex, true, m));
    s = s.replace(/\\\[([\s\S]+?)\\\]/g, (m, tex) => math(tex, true, m));
    s = s.replace(/\\\(([\s\S]+?)\\\)/g, (m, tex) => math(tex, false, m));
    s = s.replace(/(^|[^\\$\w])\$(?=[^\s$])([^$\n]*?[^\s$\\])\$(?![\w$])/g,
      (m, pre, tex) => pre + math(tex, false, '$' + tex + '$'));

    // 3. Everything else is escaped before any markup is added.
    s = esc(s);

    // 4. Links and bare URLs are held so emphasis cannot cut through them.
    s = s.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (m, label, url) => {
      const href = safeHref(unesc(url));
      if (!href) return m;
      return hold(`<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${label}</a>`);
    });
    s = s.replace(/(^|[\s(])(https?:\/\/[^\s<]*[^\s<.,;:!?)\]'"&])/g, (m, pre, url) => {
      const href = unesc(url);
      return pre + hold(`<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${url}</a>`);
    });

    // 5. Emphasis.
    s = s.replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/(^|[^\w])__(?=\S)([\s\S]*?\S)__(?!\w)/g, '$1<strong>$2</strong>');
    s = s.replace(/(^|[^*\w])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?![*\w])/g, '$1<em>$2</em>');
    s = s.replace(/(^|[^\w])_(?=[^\s_])([^_\n]*?[^\s_])_(?!\w)/g, '$1<em>$2</em>');
    s = s.replace(/~~(?=\S)([^~\n]*?\S)~~/g, '<del>$1</del>');

    // 6. Hard line breaks inside a paragraph are kept: agents mean them.
    s = s.replace(/\n/g, '<br>');

    // Restore held fragments (a link label may itself hold a code span).
    for (let i = 0; i < 4 && s.includes(HOLD); i++) {
      s = s.replace(/\u0000(\d+)\u0000/g, (_, n) => slots[Number(n)] ?? '');
    }
    return s;
  }

  // ── Blocks ────────────────────────────────────────────────────────────────
  const RE_FENCE = /^\s{0,3}(`{3,}|~{3,})\s*([\w+#.-]*)/;
  const RE_HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
  const RE_HR = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
  const RE_LIST = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
  const RE_QUOTE = /^\s{0,3}>\s?(.*)$/;
  const RE_TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

  function splitRow(line) {
    let row = line.trim();
    if (row.startsWith('|')) row = row.slice(1);
    if (row.endsWith('|') && !row.endsWith('\\|')) row = row.slice(0, -1);
    const cells = [];
    let cur = '', inCode = false;
    for (let i = 0; i < row.length; i++) {
      const ch = row[i];
      if (ch === '`') inCode = !inCode;
      if (ch === '\\' && row[i + 1] === '|') { cur += '|'; i++; continue; }
      if (ch === '|' && !inCode) { cells.push(cur.trim()); cur = ''; continue; }
      cur += ch;
    }
    cells.push(cur.trim());
    return cells;
  }

  function isBlockStart(line, next) {
    return RE_FENCE.test(line) || RE_HEADING.test(line) || RE_HR.test(line)
      || RE_LIST.test(line) || RE_QUOTE.test(line) || /^\s*(\$\$|\\\[)/.test(line)
      || (line.includes('|') && next !== undefined && RE_TABLE_SEP.test(next));
  }

  function renderList(items, state) {
    // items: [{indent, ordered, start, text}] → nested <ul>/<ol>.
    let html = '';
    const stack = [];
    const open = it => {
      const tag = it.ordered ? 'ol' : 'ul';
      const start = it.ordered && it.start !== 1 ? ` start="${it.start}"` : '';
      stack.push({ indent: it.indent, tag });
      html += `<${tag}${start}>`;
    };
    for (const it of items) {
      if (!stack.length) open(it);
      else if (it.indent > stack[stack.length - 1].indent) {
        html = html.replace(/<\/li>$/, '');
        open(it);
      } else {
        while (stack.length > 1 && it.indent < stack[stack.length - 1].indent) {
          html += `</${stack.pop().tag}></li>`;
        }
      }
      let body = it.text;
      let task = '';
      const m = /^\[([ xX])\]\s+(.*)$/s.exec(body);
      if (m) { task = m[1] === ' ' ? '<span class="md-task">&#x2610;</span> ' : '<span class="md-task done">&#x2611;</span> '; body = m[2]; }
      html += `<li>${task}${inline(body, state)}</li>`;
    }
    while (stack.length) {
      html += `</${stack.pop().tag}>`;
      if (stack.length) html += '</li>';
    }
    return html;
  }

  function blocks(src, state, depth) {
    const lines = src.split('\n');
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }

      // Fenced code. An unclosed fence (mid-stream) runs to the end.
      const fence = RE_FENCE.exec(line);
      if (fence) {
        const marker = fence[1];
        const body = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith(marker)) body.push(lines[i++]);
        i++;
        const lang = fence[2] ? ` data-lang="${esc(fence[2])}"` : '';
        out.push(`<pre class="md-pre"${lang}><code>${esc(body.join('\n'))}</code></pre>`);
        continue;
      }

      // Display math on its own lines: $$ … $$ or \[ … \].
      const mathOpen = /^\s*(\$\$|\\\[)/.exec(line);
      if (mathOpen) {
        const close = mathOpen[1] === '$$' ? '$$' : '\\]';
        const body = [];
        let rest = line.trim().slice(2);
        let j = i, closed = false, tail = '';
        for (;;) {
          const at = rest.indexOf(close);
          if (at >= 0) {
            body.push(rest.slice(0, at));
            tail = rest.slice(at + close.length).trim();
            closed = true;
            break;
          }
          body.push(rest);
          if (++j >= lines.length) break;
          rest = lines[j];
        }
        if (!closed) {
          // Unclosed (still streaming): show the source, do not swallow the log.
          out.push(`<pre class="md-pre md-math-src">${esc(lines.slice(i).join('\n'))}</pre>`);
          break;
        }
        const tex = body.join('\n').trim();
        state.math = true;
        out.push(`<div class="md-math-block">${mathSpan(tex, true, mathOpen[1] + tex + close)}</div>`);
        // Text after the closing delimiter is re-read as its own line.
        if (tail) { lines[j] = tail; i = j; } else i = j + 1;
        continue;
      }

      const heading = RE_HEADING.exec(line);
      if (heading) {
        const level = heading[1].length;
        out.push(`<div class="md-h md-h${level}">${inline(heading[2], state)}</div>`);
        i++;
        continue;
      }

      if (RE_HR.test(line)) { out.push('<hr class="md-hr">'); i++; continue; }

      // Table: header row, separator row, body rows.
      if (line.includes('|') && i + 1 < lines.length && RE_TABLE_SEP.test(lines[i + 1])) {
        const head = splitRow(line);
        const align = splitRow(lines[i + 1]).map(c =>
          c.startsWith(':') && c.endsWith(':') ? 'center' : c.endsWith(':') ? 'right' : '');
        i += 2;
        const rows = [];
        while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(splitRow(lines[i++]));
        const cell = (tag, text, k) => `<${tag}${align[k] ? ` style="text-align:${align[k]}"` : ''}>${inline(text, state)}</${tag}>`;
        out.push('<div class="md-table-wrap"><table class="md-table"><thead><tr>'
          + head.map((c, k) => cell('th', c, k)).join('') + '</tr></thead><tbody>'
          + rows.map(r => '<tr>' + head.map((_, k) => cell('td', r[k] ?? '', k)).join('') + '</tr>').join('')
          + '</tbody></table></div>');
        continue;
      }

      if (RE_QUOTE.test(line)) {
        const body = [];
        while (i < lines.length && RE_QUOTE.test(lines[i])) body.push(RE_QUOTE.exec(lines[i++])[1]);
        const innerHtml = depth < MAX_QUOTE_DEPTH ? blocks(body.join('\n'), state, depth + 1) : inline(body.join('\n'), state);
        out.push(`<blockquote class="md-quote">${innerHtml}</blockquote>`);
        continue;
      }

      if (RE_LIST.test(line)) {
        const items = [];
        while (i < lines.length) {
          const m = RE_LIST.exec(lines[i]);
          if (m) {
            const marker = m[2];
            const ordered = /\d/.test(marker);
            const indent = m[1].replace(/\t/g, '    ').length;
            // A top-level item of the other kind (- vs 1.) starts a new list.
            if (items.length && indent <= items[0].indent && ordered !== items[0].ordered) break;
            items.push({ indent, ordered,
                         start: ordered ? parseInt(marker, 10) : 1, text: m[3] });
            i++;
          } else if (lines[i].trim() && /^\s{2,}/.test(lines[i]) && items.length) {
            items[items.length - 1].text += '\n' + lines[i].trim();   // continuation
            i++;
          } else if (!lines[i].trim() && i + 1 < lines.length && RE_LIST.test(lines[i + 1])) {
            i++;   // loose list: a blank line between items
          } else break;
        }
        out.push(renderList(items, state));
        continue;
      }

      // Paragraph: until a blank line or the start of another block.
      const para = [line];
      i++;
      while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i], lines[i + 1])) para.push(lines[i++]);
      out.push(`<p>${inline(para.join('\n'), state)}</p>`);
    }
    return out.join('');
  }

  function markdown(text) {
    const state = { math: false };
    const src = String(text ?? '').replace(/\r\n?/g, '\n');
    if (src.length > MAX_RENDER_CHARS) return { html: `<p>${esc(src)}</p>`, math: false };
    return { html: blocks(src, state, 0), math: state.math };
  }

  // ── KaTeX ─────────────────────────────────────────────────────────────────
  const KATEX_OPTIONS = {
    throwOnError: false, trust: false, strict: 'ignore',
    maxSize: 20, maxExpand: 500, output: 'htmlAndMathml',
  };
  let katexState = 'idle';   // idle | loading | ready | failed

  function typeset(root) {
    if (!root || !global.katex) return;
    root.querySelectorAll('.md-math[data-tex]:not([data-typeset])').forEach(node => {
      try {
        node.innerHTML = global.katex.renderToString(node.dataset.tex,
          { ...KATEX_OPTIONS, displayMode: node.dataset.display === '1' });
        node.dataset.typeset = '1';
      } catch (_) {
        node.dataset.typeset = 'error';
        node.classList.add('md-math-error');
      }
    });
  }

  function ensureKatex() {
    if (global.katex) { katexState = 'ready'; return; }
    if (katexState !== 'idle' || !global.document || !global.document.head) return;
    katexState = 'loading';
    const doc = global.document;
    const css = doc.createElement('link');
    css.rel = 'stylesheet';
    css.href = '/vendor/katex/katex.min.css';
    doc.head.appendChild(css);
    const script = doc.createElement('script');
    script.src = '/vendor/katex/katex.min.js';
    script.onload = () => { katexState = 'ready'; typeset(doc.body); };
    script.onerror = () => { katexState = 'failed'; };
    doc.head.appendChild(script);
  }

  // Render `text` into `el` as markdown. Safe to call repeatedly while a
  // message streams in; math typesets once KaTeX is available.
  function fill(el, text) {
    const { html, math } = markdown(text);
    el.innerHTML = html;
    el.classList.add('md');
    if (math) {
      if (global.katex) typeset(el);
      else ensureKatex();
    }
    return el;
  }

  global.LogRender = { markdown, fill, typeset, esc };
})(typeof window !== 'undefined' ? window : globalThis);
