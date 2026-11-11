// The answer's Markdown, formatted (operator, 2026-11-12: "the text box
// seems to receive a md formatting that is printed as it is"): what md()
// makes of what the chat model writes, what it never makes HTML of, and the
// citations linked inside the formatted text. argv: DIR holding ask.html,
// the app's static dir.
const fs = require('fs');
const { launch } = require('./harness');
const [DIR, STATIC] = process.argv.slice(2);
let failures = 0;
function check(ok, what, detail) {
  console.log((ok ? 'PASS ' : 'FAIL ') + what + (ok || detail === undefined ? '' : ' — ' + JSON.stringify(detail)));
  if (!ok) failures++;
}

(async () => {
  const h = await launch({ width: 400, height: 800 });
  const errors = [];
  h.page.on('pageerror', e => errors.push(e.message));
  h.route(req => {
    const u = new URL(req.url);
    if (u.pathname === '/ask') return req.respond({ status: 200, contentType: 'text/html', body: fs.readFileSync(DIR + '/ask.html', 'utf8') });
    if (u.pathname.startsWith('/static/')) {
      const f = STATIC + '/' + u.pathname.slice(8);
      if (fs.existsSync(f)) return req.respond({ status: 200, contentType: f.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(f, 'utf8') });
    }
    return req.respond({ status: 200, contentType: 'text/plain', body: '' });
  });
  await h.page.goto('http://louise.test/ask');
  const got = await h.page.evaluate(() => {
    const box = document.createElement('div');
    const html = s => { box.innerHTML = md(s); return box; };
    const out = {};
    let b = html('**Dante** wrote *the* `Commedia`, ~~not~~ [Wikipedia](https://it.wikipedia.org/wiki/Dante_Alighieri).');
    out.inline = { strong: b.querySelector('p > strong')?.textContent, em: b.querySelector('em')?.textContent,
                   code: b.querySelector('code')?.textContent, del: b.querySelector('del')?.textContent,
                   href: b.querySelector('a')?.getAttribute('href'), rel: b.querySelector('a')?.getAttribute('rel'),
                   text: b.textContent };
    b = html('## Le opere\n\n- **Inferno**\n- Purgatorio\n\n3. terza\n4. quarta\n\n> citato\n\n---\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```\nx < y\n```');
    out.blocks = { h: b.querySelector('h4')?.textContent, ul: b.querySelectorAll('ul > li').length,
                   olStart: b.querySelector('ol')?.getAttribute('start'), ol: b.querySelectorAll('ol > li').length,
                   quote: b.querySelector('blockquote')?.textContent, hr: !!b.querySelector('hr'),
                   th: [...b.querySelectorAll('th')].map(e => e.textContent), td: [...b.querySelectorAll('td')].map(e => e.textContent),
                   pre: b.querySelector('pre code')?.textContent };
    b = html('Riga uno\nriga due\n\nAltro paragrafo');
    out.lines = { p: b.querySelectorAll('p').length, br: b.querySelectorAll('br').length };
    // nothing the model writes, nor a passage it quotes, becomes HTML of its own
    b = html('<script>alert(1)</script> <img src=x onerror="alert(2)"> [x](javascript:alert(3)) '
             + '[y](https://e.org/"onmouseover="alert(4)) **<b onclick=alert(5)>b</b>**');
    out.unsafe = { tags: [...b.querySelectorAll('*')].map(e => e.tagName.toLowerCase()),
                   handlers: [...b.querySelectorAll('*')].some(e => [...e.attributes].some(a => a.name.startsWith('on'))),
                   text: b.textContent };
    out.partial = html('Hello **wo').textContent;
    out.escaped = html('5 \\* 3 and \\*not italic\\*').textContent;
    // the answer on the page: formatted, its citations linked outside code, its source kept
    const hits = document.getElementById('hits') || document.body.appendChild(Object.assign(document.createElement('div'), { id: 'hits' }));
    hits.innerHTML = '<div class="hit"></div><div class="hit"></div>';
    const src = '**Dante** wrote it [1].\n\n- see [2]\n- `[1]` in code';
    setAnswer(src); linkCitations();
    const a = document.getElementById('answer');
    out.page = { strong: a.querySelector('strong')?.textContent, cites: [...a.querySelectorAll('a.cite')].map(e => e.textContent),
                 codeKept: a.querySelector('code')?.textContent, source: answerSource() === src,
                 noStars: !a.textContent.includes('*'), whiteSpace: getComputedStyle(a).whiteSpace };
    linkCitations();
    out.page.twice = a.querySelectorAll('a.cite').length;
    return out;
  });
  check(got.inline.strong === 'Dante' && got.inline.em === 'the' && got.inline.code === 'Commedia' && got.inline.del === 'not',
        'bold, italic, code, strikethrough', got.inline);
  check(got.inline.href === 'https://it.wikipedia.org/wiki/Dante_Alighieri' && /noopener/.test(got.inline.rel || ''),
        'an http(s) link, opened apart', got.inline);
  check(!got.inline.text.includes('*') && !got.inline.text.includes('`'), 'no mark left in the text', got.inline.text);
  const bl = got.blocks;
  check(bl.h === 'Le opere' && bl.ul === 2 && bl.ol === 2 && bl.olStart === '3' && bl.quote === 'citato' && bl.hr,
        'headings, lists (numbered from where they start), quotes, rules', bl);
  check(JSON.stringify(bl.th) === '["A","B"]' && JSON.stringify(bl.td) === '["1","2"]' && bl.pre === 'x < y',
        'tables and code blocks', bl);
  check(got.lines.p === 2 && got.lines.br === 1, 'paragraphs, and a line break inside one', got.lines);
  check(got.unsafe.tags.every(t => ['p', 'strong', 'a'].includes(t)) && !got.unsafe.handlers,
        'no script, image, handler or javascript: link is made', got.unsafe);
  check(got.unsafe.text.includes('<script>alert(1)</script>') && got.unsafe.text.includes('[x](javascript:alert(3))'),
        'what is not Markdown is shown as the text it is', got.unsafe.text);
  check(got.partial === 'Hello **wo', 'an emphasis still being written stays as written until it closes', got.partial);
  check(got.escaped === '5 * 3 and *not italic*', 'an escaped mark is its character', got.escaped);
  const p = got.page;
  check(p.strong === 'Dante' && JSON.stringify(p.cites) === '["[1]","[2]"]' && p.codeKept === '[1]',
        'the answer is formatted and its citations linked, not inside code', p);
  check(p.source && p.noStars && p.whiteSpace !== 'pre-wrap', 'its source is kept as written; no mark on the page', p);
  check(p.twice === 2, 'linking twice links once', p.twice);
  check(errors.length === 0, 'no page errors', errors);
  console.log(failures ? `${failures} FAILED` : 'ALL PASSED');
  await h.close();
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
