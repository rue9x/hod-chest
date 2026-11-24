// Polls the watcher's /state.json and redraws the table.
const POLL_MS = 1000;
const STALE_S = 15;

const $ = id => document.getElementById(id);
let lastRows = '';                  // the rows last drawn, so an open dropdown isn't rebuilt under you

// Same matching as chest_watch.py's squash(): no case, spaces, apostrophes or possessive 's.
const squash = t => t.toLowerCase().replace(/['’]s/g, '').replace(/['’\s]+/g, '');

function wantsOf(row) {
  return row.want === 'None' ? [] : row.want.split(',').map(squash).filter(Boolean);
}

function isWanted(row, item) {
  const s = squash(item);
  return wantsOf(row).some(w => s.includes(w));
}

function manipClass(text) {
  if (text.startsWith('YES')) return 'yes-now';
  if (text.startsWith('yes')) return 'yes';
  if (text.startsWith('maybe')) return 'maybe';
  return 'none';
}

function cell(label, text, cls) {
  const td = document.createElement('td');
  td.dataset.label = label;
  td.textContent = text;
  if (cls) td.className = cls;
  return td;
}

// Desired drop: a dropdown of this player's possible drops; picking one sends it to the watcher.
function wantCell(row) {
  const td = document.createElement('td');
  td.dataset.label = 'Desired drop';
  const sel = document.createElement('select');
  sel.setAttribute('aria-label', `Desired drop for ${row.player}`);
  const add = (value, text) => {
    const o = document.createElement('option');
    o.value = value;
    o.textContent = text;
    sel.append(o);
    return o;
  };
  add('', 'None');
  const items = [...new Set((row.odds || []).map(([item]) => item))].sort((a, b) => a.localeCompare(b));
  const current = row.want === 'None' ? '' : row.want;
  let picked = current ? items.find(i => squash(i) === squash(current)) : '';
  if (current && !picked) {
    add(current, current);          // a target typed by hand (several, or a partial name)
    picked = current;
  }
  for (const item of items) add(item, item);
  sel.value = picked || '';
  sel.addEventListener('change', () => save(sel, { field: 'want', key: row.key, value: sel.value }));
  td.append(sel);
  return td;
}

// Sends one setting to the watcher. Settings live in the watcher's memory only and reset when it restarts.
async function save(input, change) {
  input.disabled = true;
  try {
    const res = await fetch('set', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(change),
    });
    const out = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(out.said || 'HTTP ' + res.status);
    lastRows = '';                  // redraw with the watcher's next record
  } catch (err) {
    $('warn').hidden = false;
    $('warn').textContent = `Couldn't save: ${err.message || err}`;
  } finally {
    input.disabled = false;
    input.blur();                   // let the next record redraw the table
  }
}

const ACC_CHOICES = ['', '0/0', '1/0', '2/0', '0/1', '1/1', '2/1', '0/2', '1/2', '2/2'];

function field(labelText, input) {
  const label = document.createElement('label');
  label.className = 'field';
  const span = document.createElement('span');
  span.textContent = labelText;
  label.append(span, input);
  return label;
}

function numberInput(row, name, placeholder) {
  const input = document.createElement('input');
  input.type = 'number';
  input.min = '0';
  input.max = '999';
  input.inputMode = 'numeric';
  input.placeholder = placeholder;
  input.value = (row.settings || {})[name] || '';
  input.addEventListener('change', () => save(input, { field: name, key: row.key, value: input.value }));
  return input;
}

// What the gear suggestions may ask of this player.
function settingsFor(row) {
  const set = row.settings || {};
  const box = document.createElement('div');
  box.className = 'settings';

  box.append(field('Luck cap', numberInput(row, 'maxluck', `Luck + 40`)));

  const acc = document.createElement('select');
  for (const v of ACC_CHOICES) {
    const o = document.createElement('option');
    o.value = v;
    o.textContent = v ? `${v.split('/')[0]} Thief Ring/Ebisu, ${v.split('/')[1]} Soul Eater` : 'Whatever they can equip';
    acc.append(o);
  }
  acc.value = set.maxacc || '';
  acc.addEventListener('change', () => save(acc, { field: 'maxacc', key: row.key, value: acc.value }));
  box.append(field('Luck items owned', acc));

  if (row.key !== 'you') {
    box.append(field('Luck', numberInput(row, 'luck', 'read from game')));
    const owns = document.createElement('input');
    owns.type = 'checkbox';
    owns.checked = set.owns === 'all';
    owns.addEventListener('change', () =>
      save(owns, { field: 'owns', key: row.key, value: owns.checked ? 'all' : '' }));
    box.append(field('Owns every luck item', owns));
    box.append(field('Luck with no luck gear', numberInput(row, 'base', 'for outfits')));
  }
  return box;
}

function clock(sec) {
  return sec ? new Date(sec * 1000).toLocaleTimeString() : 'never';
}

function renderStatus(s, error) {
  const status = $('status');
  const fresh = !error && s.now - (s.at || 0) < STALE_S;
  const live = fresh && (s.state === 'live' || s.state === 'no chest');
  status.className = 'status ' + (live ? 'live' : 'off');
  if (error) status.textContent = 'Not live: the watcher is not running (' + error + ')';
  else if (!fresh) status.textContent = 'Not live: the watcher stopped reporting';
  else if (live) status.textContent = s.state === 'no chest' ? 'Live: no boss chest in this stage yet' : 'Live';
  else status.textContent = 'Not live: ' + (s.note || s.state);

  $('updated').textContent = error ? '' : `Updated ${clock(s.changed)} (checked ${clock(s.at)})`;
  $('head').hidden = !s.head;
  $('head').textContent = s.head || '';
  $('warn').hidden = !s.warn;
  $('warn').textContent = s.warn || '';
}

function renderRows(rows) {
  const body = $('rows');
  body.replaceChildren();
  if (!rows.length) {
    const tr = document.createElement('tr');
    const td = cell('', "No players yet. They show up once you're in a stage with a boss chest.", 'muted');
    td.colSpan = 6;
    tr.append(td);
    body.append(tr);
    return;
  }
  for (const r of rows) {
    const tr = document.createElement('tr');
    if (r.manip.startsWith('YES')) tr.className = 'hit';
    tr.append(
      cell('Player', r.player),
      cell('Character', r.char),
      cell('Luck', r.luck, 'num'),
      wantCell(r),
      cell('Can manipulate', r.manip, manipClass(r.manip)),
      cell('Expected drop', r.drop, 'drop' + (isWanted(r, r.drop) ? ' yes-now' : '')),
    );
    body.append(tr);
  }
}

function renderLists(rows) {
  const box = $('lists');
  box.replaceChildren();
  for (const r of rows) {
    const h = document.createElement('h2');
    h.textContent = `${r.player} (${r.char})`;
    const ul = document.createElement('ul');
    ul.className = 'odds';
    for (const [item, pct] of r.odds || []) {
      const li = document.createElement('li');
      li.textContent = `${item} ${pct}%`;
      if (item === r.drop) li.classList.add('expected');
      if (isWanted(r, item)) li.classList.add('wanted');
      ul.append(li);
    }
    box.append(h, settingsFor(r), ul);
  }
}

async function poll() {
  try {
    const res = await fetch('state.json', { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const s = await res.json();
    renderStatus(s);
    const rows = s.rows || [];
    const key = JSON.stringify(rows);
    const busy = document.activeElement && ['SELECT', 'INPUT'].includes(document.activeElement.tagName);
    if (key !== lastRows && !busy) {
      lastRows = key;
      renderRows(rows);
      renderLists(rows);
    }
  } catch (err) {
    renderStatus({}, err.message || String(err));
  } finally {
    setTimeout(poll, POLL_MS);
  }
}

// Ends the watcher process (and this page's server). /chest in Claude Code, or running
// chest_watch.py again, starts it back up; settings reset.
$('stop').addEventListener('click', async () => {
  if (!confirm('Stop the watcher? The page goes offline and settings reset. /chest starts it again.')) return;
  const btn = $('stop');
  btn.disabled = true;
  try {
    await fetch('quit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  } catch {
    // it may close the connection as it exits
  }
  btn.textContent = 'Stopped';
});

poll();
