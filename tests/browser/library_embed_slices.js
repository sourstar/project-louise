// The grow dialog says what a check means (operator, 2026-11-07: "For
// wikidata the archive is unique, thus it makes no sense to show a false
// choice, but it makes sense to show the embedding options there … a short
// comment or tag about the meaning of checking/unchecking (download+embed,
// or embed)", "Like at the top of the checkboxes").
//
//   node library_embed_slices.js DIR STATIC  — run by tests/test_library_embed_slices_browser.py
//
// DIR holds library.html as the test rendered it: a one-archive card
// ("wikidata"), a separate-downloads card ("wikipedia"), one holding
// everything ("wikipedia-all") and one with extra formats ("gutenberg").
// Every request is answered here; a POST is recorded, never sent. Each
// dialog is read in document order — the group tags and the checkboxes —
// then wikidata's embed group is changed and saved. Prints one JSON line.
const fs = require('fs');
const { launch } = require('./harness');
const [DIR, STATIC] = process.argv.slice(2);
const BASE = 'http://louise.test';
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const h = await launch({ width: 1100, height: 900 });
  const posts = [];
  h.route(req => {
    const u = new URL(req.url);
    if (req.method === 'POST') {
      posts.push({ path: u.pathname, body: req.postData || '' });
      return req.respond({ status: 200, contentType: 'application/json', body: '{"ok":true,"line":"saved"}' });
    }
    if (u.pathname === '/admin/library')
      return req.respond({ status: 200, contentType: 'text/html', body: fs.readFileSync(DIR + '/library.html', 'utf8') });
    if (u.pathname.startsWith('/static/')) {
      const f = STATIC + '/' + u.pathname.slice(8);
      if (fs.existsSync(f)) return req.respond({ status: 200, contentType: f.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(f, 'utf8') });
    }
    return req.respond({ status: 200, contentType: 'text/plain', body: '' });
  });
  const page = h.page;
  // the dialog in document order: each group tag, each checkbox
  const dialog = () => page.evaluate(() => {
    const m = document.getElementById('modal-inner');
    const b = document.getElementById('modal-backdrop');
    return {
      shown: getComputedStyle(b).display !== 'none',
      text: m.innerText.replace(/\s+/g, ' ').trim(),
      order: [...m.querySelectorAll('[data-group-tag], input[type=checkbox]')].map(e =>
        e.matches('[data-group-tag]') ? { tag: e.textContent.trim() }
          : { box: e.className || e.id, value: e.value === 'on' ? null : e.value,
              checked: e.checked, disabled: e.disabled }),
      buttons: [...m.querySelectorAll('button')].map(x => x.textContent.trim()),
      focused: document.activeElement && document.activeElement.id,
    };
  });
  const open = async name => { await page.evaluate(n => openGrow(n), name); await sleep(100); return dialog(); };
  const click = async sel => { await page.click(sel); await sleep(150); };
  const out = {};
  await page.goto(BASE + '/admin/library', { waitUntil: 'load' });
  await sleep(200);

  for (const name of ['wikidata', 'wikipedia', 'wikipedia-all', 'gutenberg']) out[name] = await open(name);

  // wikidata: property out, taxon in — the confirmation says both, then posts
  await open('wikidata');
  await click('input.embed_slice[value="family=property"]');
  await click('input.embed_slice[value="family=taxon"]');
  await page.evaluate(() => saveEmbedSlices('wikidata'));
  await sleep(150);
  out.confirm = await dialog();
  await click('#confirm-go');
  for (let i = 0; i < 30 && posts.length < 1; i++) await sleep(100);
  out.posted = posts.slice();

  // every family held: the rows go checked and grey, and what is declined comes back
  await page.goto(BASE + '/admin/library', { waitUntil: 'load' });
  await sleep(200);
  await open('wikidata');
  await click('#embed_slices_all');
  out.every = await dialog();
  await page.evaluate(() => saveEmbedSlices('wikidata'));
  await sleep(150);
  out.everyConfirm = await dialog();
  await click('#confirm-cancel');
  out.afterCancel = { shown: (await dialog()).shown, posts: posts.length };

  // none checked: said in the dialog, nothing posted
  await open('wikidata');
  await page.evaluate(() => document.querySelectorAll('input.embed_slice:checked').forEach(e => e.click()));
  await page.evaluate(() => saveEmbedSlices('wikidata'));
  await sleep(150);
  out.none = { text: (await dialog()).text, posts: posts.length };

  console.log(JSON.stringify(out));
  await h.close();
})().catch(e => { console.error(e); process.exit(1); });
