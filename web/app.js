// Polls the watcher's /state.json and redraws the table.
const POLL_MS = 1000;
const STALE_S = 15;

const $ = id => document.getElementById(id);
let lastRows = '';                  // the rows last drawn, so an open dropdown isn't rebuilt under you
let touched = 0;                    // when a control was last used; a focused but idle one doesn't block redraws
for (const ev of ['pointerdown', 'keydown', 'input', 'focusin']) {
  document.addEventListener(ev, () => { touched = Date.now(); }, true);
}
// Escape closes a dropdown without a change: let go of it, so the table redraws right away.
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && document.activeElement && document.activeElement.tagName === 'SELECT') {
    document.activeElement.blur();
  }
});

// Same matching as chest_watch.py's squash(): no case, spaces, apostrophes or possessive 's.
const squash = t => t.toLowerCase().replace(/['’]s/g, '').replace(/['’\s]+/g, '');

// Same as chest_watch.py's matches(): a substring, or with a leading '=' (a pick from the list) that
// exact item only.
function matches(target, name) {
  // Both forms of a possessive, as chest_watch.py's forms(): without the 's, and with the s kept.
  const forms = t => [squash(t), t.toLowerCase().replace(/['\u2019\s]+/g, '')];
  const exact = squash(target).startsWith('=');
  const ts = forms(target).map(t => t.replace(/^=+/, '')).filter(Boolean);
  const ns = forms(name || '').filter(Boolean);
  return ts.some(t => ns.some(n => (exact ? t === n : n.includes(t))));
}

function isWanted(row, item) {
  return (row.targets || []).some(t => matches(t, item));
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
  // Every item their chest can give (known even when their Luck isn't, so odds are empty).
  const items = [...new Set(row.items || (row.odds || []).map(([item]) => item))].sort((a, b) => a.localeCompare(b));
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
    showError('');                  // the watcher's next record redraws with the change
  } catch (err) {
    showError(`Couldn't save: ${err.message || err}`);
    lastRows = '';                  // put the input back to what the watcher has
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
  input.defaultValue = input.value = (row.settings || {})[name] || '';   // defaultValue: what's saved
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
    const worn = document.createElement('select');
    for (const [v, text] of [['', 'read from game'], ['0', 'none'], ['1', '1'], ['2', '2']]) {
      const o = document.createElement('option');
      o.value = v;
      o.textContent = text;
      worn.append(o);
    }
    worn.value = set.acc || '';
    worn.addEventListener('change', () => save(worn, { field: 'acc', key: row.key, value: worn.value }));
    box.append(field('Thief Rings / Ebisu worn', worn));
    const wornB = document.createElement('select');
    for (const [v, text] of [['', 'read from game'], ['0', 'none'], ['1', '1'], ['2', '2']]) {
      const o = document.createElement('option');
      o.value = v;
      o.textContent = text;
      wornB.append(o);
    }
    wornB.value = set.accb || '';
    wornB.addEventListener('change', () => save(wornB, { field: 'accb', key: row.key, value: wornB.value }));
    box.append(field('Soul Eater Rings worn', wornB));
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

// A failed save or stop stays on screen until the next one succeeds.
function showError(text) {
  $('error').hidden = !text;
  $('error').textContent = text;
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
  const warn = [s.warn, s.web].filter(Boolean).join(' · ');
  $('warn').hidden = !warn;
  $('warn').textContent = warn;
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
      cell('Expected drop', r.drop, 'drop' + (!r.drop.startsWith('by ') && isWanted(r, r.drop) ? ' yes-now' : '')),
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

function clearRows() {
  if (lastRows === 'cleared') return;
  lastRows = 'cleared';
  renderRows([]);
  renderLists([]);
}

async function poll() {
  try {
    const res = await fetch('state.json', { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const s = await res.json();
    seenAt = Date.now();
    if (s.state !== 'stopped') resetStop();
    renderStatus(s);
    if (s.now - (s.at || 0) >= STALE_S) {   // a watcher that stopped reporting: don't show its old verdict
      clearRows();
      return;
    }
    const rows = s.rows || [];
    const key = JSON.stringify(rows);
    // Hold redraws only while a control is actually being used: a dropdown closed with Escape keeps
    // focus, and the table must not freeze on an old verdict because of it.
    // A dropdown gets a little longer (moving through its options fires no events), but not so long
    // that the previous load's verdict lingers after clicking the option already chosen.
    const el = document.activeElement;
    const busy = el && (el.tagName === 'SELECT' ? Date.now() - touched < 8000
      : el.tagName === 'INPUT' && (Date.now() - touched < 4000
        || (el.type === 'number' && el.value !== el.defaultValue)));  // unsaved typing in a number box
    if (key !== lastRows && !busy) {
      lastRows = key;
      renderRows(rows);
      renderLists(rows);
    }
  } catch (err) {
    renderStatus({}, err.message || String(err));
    clearRows();                    // the watcher is gone: its last table (a YES, maybe) no longer holds
  } finally {
    setTimeout(poll, POLL_MS);
  }
}

// Ends the watcher process (and this page's server). Running chest_watch.py again, or /chest in
// Claude Code, starts it back up; settings reset.
let stopped = false;                // Stop succeeded; cleared once a live watcher answers again
let seenAt = 0;                     // when the watcher last answered a poll

$('stop').addEventListener('click', async () => {
  if (!confirm('Stop the watcher? This page goes offline and settings reset. ' +
               'Run chest_watch.py again (or /chest in Claude Code) to start it back up.')) return;
  const btn = $('stop');
  btn.disabled = true;
  let res;
  try {
    res = await fetch('quit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  } catch {
    res = null;                     // no reply: it stopped as it answered, or there was nothing to stop
  }
  if (!res && Date.now() - seenAt > 5000) {
    showError("There's no watcher to stop: it isn't running.");
    btn.disabled = false;
    return;
  }
  if (res && !res.ok) {
    const out = await res.json().catch(() => ({}));
    showError(`Couldn't stop the watcher: ${out.said || 'HTTP ' + res.status}`);
    btn.disabled = false;
    return;
  }
  stopped = true;
  btn.textContent = 'Stopped';
});

// The Stop button comes back once a running watcher answers again.
function resetStop() {
  if (!stopped) return;
  stopped = false;
  $('stop').disabled = false;
  $('stop').textContent = 'Stop watcher';
}

// Cheats: saved positions (cheats/positions.py) to move to. They write to the game.
let cheatKey = '';

async function loadCheats() {
  try {
    const res = await fetch('cheats.json', { cache: 'no-store' });
    if (res.status === 404) {       // a watcher started before cheats existed: it needs a restart
      $('cheat-said').textContent = 'This watcher is older than the Cheats section: restart it (/chest, or run chest_watch.py again).';
      $('cheat-said').className = 'error';
      return;
    }
    const { cheats = [] } = await res.json();
    const key = JSON.stringify(cheats);
    const sel = $('cheat');
    if (key !== cheatKey && document.activeElement !== sel) {
      cheatKey = key;
      const was = sel.value;
      sel.replaceChildren();
      if (!cheats.length) {
        const o = document.createElement('option');
        o.value = '';
        o.textContent = 'No saved cheats yet (python3 cheats/positions.py save NAME)';
        sel.append(o);
      }
      for (const c of cheats) {
        const o = document.createElement('option');
        o.value = c.name;
        o.textContent = `Move to ${c.name} (X ${c.x}, Y ${c.y})`;
        sel.append(o);
      }
      if (cheats.some(c => c.name === was)) sel.value = was;
      $('activate').disabled = !cheats.length;
    }
  } catch {
    // watcher gone: the status line already says so
  } finally {
    setTimeout(loadCheats, 5000);
  }
}

$('activate').addEventListener('click', async () => {
  const name = $('cheat').value;
  if (!name) return;
  const btn = $('activate');
  btn.disabled = true;
  $('cheat-said').textContent = 'Working…';
  try {
    const res = await fetch('cheat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'position', name }),
    });
    const out = await res.json().catch(() => ({}));
    $('cheat-said').textContent = out.said || (res.ok ? 'Done.' : 'HTTP ' + res.status);
    $('cheat-said').className = res.ok ? 'muted' : 'error';
  } catch (err) {
    $('cheat-said').textContent = `Couldn't run it: ${err.message || err}`;
    $('cheat-said').className = 'error';
  } finally {
    btn.disabled = false;
  }
});

// Chest loot: make every chest drop one item, or put them back.
let allItems = [];

function fillItems() {
  const sel = $('loot-item');
  const want = squash($('loot-filter').value);
  const was = sel.value;
  const shown = allItems.filter(name => !want || squash(name).includes(want));
  sel.replaceChildren();
  for (const name of shown.slice(0, 500)) {
    const o = document.createElement('option');
    o.value = name;
    o.textContent = name;
    sel.append(o);
  }
  if (!shown.length) {
    const o = document.createElement('option');
    o.value = '';
    o.textContent = allItems.length ? 'No item matches the filter' : 'No item names yet (enter a stage)';
    sel.append(o);
  }
  if (shown.includes(was)) sel.value = was;
  $('loot-activate').disabled = !sel.value;
}

async function loadItems() {
  try {
    const res = await fetch('items.json', { cache: 'no-store' });
    if (res.ok) allItems = (await res.json()).items || [];
  } catch {
    // watcher gone: the status line already says so
  }
  fillItems();
  if (!allItems.length) setTimeout(loadItems, 5000);   // names not built yet: look again
}

async function runCheat(btn, change) {
  btn.disabled = true;
  $('cheat-said').textContent = 'Working…';
  try {
    const res = await fetch('cheat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(change),
    });
    const out = await res.json().catch(() => ({}));
    $('cheat-said').textContent = out.said || (res.ok ? 'Done.' : 'HTTP ' + res.status);
    $('cheat-said').className = res.ok ? 'muted' : 'error';
  } catch (err) {
    $('cheat-said').textContent = `Couldn't run it: ${err.message || err}`;
    $('cheat-said').className = 'error';
  } finally {
    btn.disabled = false;
  }
}

$('loot-filter').addEventListener('input', fillItems);
$('loot-item').addEventListener('change', () => { $('loot-activate').disabled = !$('loot-item').value; });
$('loot-activate').addEventListener('click', () => {
  const item = $('loot-item').value;
  if (item) runCheat($('loot-activate'), { kind: 'loot', item, all: $('loot-kind').value === 'all' });
});
$('loot-reset').addEventListener('click', () => runCheat($('loot-reset'), { kind: 'reset' }));
$('fill-hp').addEventListener('click', () => runCheat($('fill-hp'), { kind: 'fill-hp' }));
$('fill-mp').addEventListener('click', () => runCheat($('fill-mp'), { kind: 'fill-mp' }));
$('mastery-activate').addEventListener('click', async () => {
  $('mastery-deactivate').disabled = false;       // while it waits, it can be called off
  const done = runCheat($('mastery-activate'), { kind: 'mastery' });
  $('cheat-said').textContent = 'Waiting for your next sub-weapon hit (up to 60 seconds)…';
  await done;
  $('mastery-deactivate').disabled = true;
});
$('mastery-deactivate').addEventListener('click', async () => {
  $('mastery-deactivate').disabled = true;
  try {
    await fetch('cheat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'mastery-cancel' }),
    });
  } catch {
    // the waiting request reports the outcome either way
  }
});

loadItems();
loadCheats();
poll();
