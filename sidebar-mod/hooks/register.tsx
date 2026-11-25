import { atom, read, update } from 'claude-code'
import type { EngineInterface as Api, HookStream, ProcessSpawnChunk, ProcessSpawnResult, Register } from 'claude-code'
import type { Row } from '../types'

const PANE = 'hod-chest'
const TITLE = 'Boss chest'
const HIT = '*** TARGET HIT'
const REACH = '+++ TARGET REACHABLE'
const PYTHONS: string[][] = [['python3'], ['py', '-3'], ['python']]
// The watcher also serves the live table as a web page here.
const PORT = 8765
const URL = `http://localhost:${PORT}/`
// The watcher reports at least every 3 s; this long without a record means it has stalled.
const STALE_MS = 15000
// The sidebar talks to it over IPv4 directly: the server binds 127.0.0.1 only, and localhost may
// resolve to ::1 first.
const API = `http://127.0.0.1:${PORT}/`

const watch = atom(
  { plugin: 'hod-chest', key: 'watch' } as const,
  { state: '', note: '', head: '', warn: '', changed: 0, at: 0, rows: [] as Row[], status: 'not started', active: false },
  { shape: 'watch-5' },
)

// Module-local: a reload kills the child with the module, so this starts over too.
// (A reload is also how a changed chest_watch.py gets picked up: session.start restarts it.)
let running = false
// The watcher said it is stopping (the page's Stop); a /chest meanwhile starts it again after.
let stopping = false
let startAgain = false
// The running watcher's id, from its records: sent with each change so only it accepts them.
let token = ''
// The running watcher, so /chest can stop it, and when it started (ms) to spot code changed since.
let child: HookStream<ProcessSpawnChunk, ProcessSpawnResult> | undefined
let startedAt = 0
const CODE = ['chest_watch.py', 'pool.py', 'gmem.py', 'gen_item_names.py', 'build_names.py', 'txrc.py',
  'cheats/positions.py', 'cheats/loot.py', 'cheats/mastery.py', 'cheats/health.py']

// True when a Python file the watcher loaded changed after it started.
async function codeChanged($: Api): Promise<boolean> {
  const script = await findScript($)
  if (!script || !startedAt) return false
  for (const f of CODE) {
    try {
      if ((await $.fs.stat(`${dirOf(script)}/${f}`)).mtimeMs > startedAt) return true
    } catch {
      // missing file; ignore
    }
  }
  return false
}

// The hit line last notified about; it names the load number, so each new hit differs.
let lastHit = ''

function dirOf(path: string): string {
  return path.slice(0, Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\')))
}

// Settings live in the watcher's memory only (they reset when it restarts); the watcher's page
// server takes changes at POST /set, the same as the web page's controls.
async function send($: Api, change: Record<string, string>): Promise<{ ok: boolean; said: string }> {
  // Wait for the watcher's first record, so the change carries its id and only it accepts the change.
  for (let i = 0; i < 600 && running && !token; i++) await $.clock.sleep(100)
  if (!token)
    return {
      ok: false,
      said: running
        ? 'The watcher is still starting up; try again in a moment.'
        : "The sidebar's watcher isn't running, so nothing was changed. Run /chest to start it.",
    }
  let last = ''
  for (let i = 0; i < 20; i++) {   // the watcher may still be starting its server
    try {
      const r = await $.http.fetch(`${API}set`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Origin: `http://127.0.0.1:${PORT}` },
        body: JSON.stringify(token ? { ...change, token } : change),
      })
      let out: { said?: string } = {}
      try {
        out = JSON.parse(r.text || '{}') as { said?: string }
      } catch {
        // not JSON: fall back to the status
      }
      return { ok: r.ok, said: out.said ?? (r.ok ? 'Saved.' : `The watcher refused it (HTTP ${r.status}).`) }
    } catch (err) {
      last = String(err)
      await $.clock.sleep(250)
    }
  }
  return { ok: false, said: `Couldn't reach the watcher at ${URL}: ${last}` }
}

const NOT_SAVED = 'Settings reset when the watcher restarts.'

// `/chest want a, Gamertag: b` sets the whole desired-drop list; `/chest want` clears it.
async function setWants($: Api, args: string): Promise<string> {
  const { ok, said } = await send($, { field: 'wants', value: args.trim() })
  return ok && args.trim() ? `${said}. Reload the stage until Can manipulate says YES or yes.` : said
}

// `/chest maxluck N` caps the Luck the gear suggestions may ask of you; `/chest maxluck` resets it.
async function setCap($: Api, args: string): Promise<string> {
  const n = args.trim()
  if (n && !/^\d+$/.test(n)) return 'Give the cap as a whole number, like /chest maxluck 80.'
  const r = await send($, { field: 'maxluck', key: 'you', value: n })
  if (!r.ok) return r.said
  return n ? `Suggestions now stay at or below LCK ${n}. ${NOT_SAVED}` : 'Luck cap reset to your current Luck + 40.'
}

// `/chest maxacc A/B`: how many type-A / type-B luck items you own; `/chest maxacc` resets it.
async function setAcc($: Api, args: string): Promise<string> {
  const v = args.trim()
  if (v && !/^[0-2](\/[0-2])?$/.test(v)) return 'Give it as A/B, each 0-2, like /chest maxacc 1/0.'
  const r = await send($, { field: 'maxacc', key: 'you', value: v })
  if (!r.ok) return r.said
  const saved = /maxacc (\d\/\d)/.exec(r.said)?.[1] ?? v     // as the watcher stored it: '2' is 2/0
  return v
    ? `Suggestions now use at most ${saved} luck items (type A: Thief Ring, Ebisu Raiment / type B: Soul Eater Ring). ${NOT_SAVED}`
    : 'Luck-item limit reset to what your character can equip.'
}

async function noteHit($: Api, head: string) {
  if (!head.startsWith(HIT) && !head.startsWith(REACH)) return
  // A hit is new whenever who gets what changes; for "+++ TARGET REACHABLE (load #3)" only the load
  // counts, since its gear suggestions reorder as gear changes.
  const verdict = head.startsWith(HIT) ? head : (/^\S+ TARGET \w+ \(load #\d+\)/.exec(head)?.[0] ?? head)
  if (verdict === lastHit) return
  lastHit = verdict
  const text = head.replace(/^[*+]+ /, '')
  $.ui.toast(text)
  await $.ui.notify(text, { title: 'Boss chest' })
}

async function findPython($: Api): Promise<string[] | undefined> {
  for (const py of PYTHONS) {
    try {
      const r = await $.process.run([...py, '--version'], { timeoutMs: 5000 })
      if (r.exitCode === 0 && /Python 3\./.test(r.stdout + r.stderr)) return py
    } catch {
      // not installed; try the next
    }
  }
  return undefined
}

// chest_watch.py beside the mod's folder (sidebar-mod/ inside the project),
// else in the session's folder.
async function findScript($: Api): Promise<string | undefined> {
  const tries: string[] = []
  try {
    const { realPath } = await $.fs.stat($.plugin.root, { resolve: true })
    tries.push(`${realPath ?? $.plugin.root}/../chest_watch.py`)
  } catch {
    tries.push(`${$.plugin.root}/../chest_watch.py`)
  }
  tries.push(`${await $.session.cwd()}/chest_watch.py`)
  for (const path of tries) {
    try {
      const s = await $.fs.stat(path, { resolve: true })
      if (s.realPath) return s.realPath
    } catch {
      // missing; try the next
    }
  }
  return undefined
}

async function setStatus($: Api, status: string) {
  await update($, watch, w => ({ ...w, status }))
}

async function start($: Api) {
  let tick: { cancel: () => void } | undefined     // the pane's redraw timer while this watcher runs
  if (running) {
    if (stopping) startAgain = true
    return
  }
  stopping = false
  running = true
  // Running now: a mod reload should bring it back (set here, after any old watcher's exit cleared it).
  // a new watcher: the old one's report times would read as "stopped reporting" until it speaks
  await update($, watch, w => ({ ...w, active: true, at: 0, changed: 0 }))
  try {
    const py = await findPython($)
    if (!py) return void (await setStatus($, 'no Python 3 found (tried python3, py -3, python)'))
    const script = await findScript($)
    if (!script) return void (await setStatus($, 'chest_watch.py not found beside the mod or in the session folder'))
    const dir = dirOf(script)
    await setStatus($, `starting ${py.join(' ')} chest_watch.py`)

    // UTF-8 on every OS: Windows would otherwise pipe item names in its legacy code page.
    const proc = $.process.spawn({
      argv: [...py, '-u', script, '--json', '--serve', String(PORT)],
      cwd: dir,
      env: { PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
    })
    child = proc
    startedAt = Date.now()
    // The pane only redraws on a record; while the watcher runs, redraw every few seconds too,
    // so a watcher that hangs without exiting shows as stale instead of a frozen "Live".
    tick = $.clock.every(5000, () => $.ui.invalidate('ui.render'))
    let buf = ''
    let errTail = ''
    for await (const { stream, text } of proc) {
      if (stream === 'stderr') {
        errTail = (errTail + text).slice(-2000)
        if (errTail.includes('stopped from the web page')) {
          stopping = true
          token = ''        // a change sent now waits for the next watcher, not this one
        }
        continue
      }
      // One JSON record per line; only the newest whole one matters.
      buf += text
      const lines = buf.split('\n')
      buf = lines.pop() ?? ''
      const last = lines.filter(l => l.trim()).pop()
      if (!last) continue
      let rec: { state: string; note: string; head: string; warn?: string; web?: string; token?: string; changed: number; rows: Row[] }
      try {
        rec = JSON.parse(last)
      } catch {
        continue
      }
      await update($, watch, w => ({
        ...w,
        state: rec.state,
        note: rec.note,
        head: rec.head,
        warn: [rec.warn, rec.web].filter(Boolean).join(' · '),
        rows: rec.rows,
        changed: Math.round(rec.changed * 1000),
        status: 'running',
        at: Date.now(),
      }))
      if (rec.token && child === proc && !stopping) token = rec.token     // not from one being replaced or stopped
      if (rec.state === 'stopped') {
        stopping = true
        token = ''
      }
      await noteHit($, rec.head)
    }
    const end = await proc.result.catch(() => undefined)
    const why = end?.signal ? `signal ${end.signal}` : `code ${end?.code}`
    const err = errTail.trim().split('\n').slice(-3).join(' | ')
    // Its last table (a YES, maybe) no longer holds: clear it along with saying why it stopped.
    // `state: ''` also keeps the pane from reading Live before `running` clears below.
    if (stopping && !startAgain) {
      // Stopped on purpose: don't bring it back on a mod reload, only on /chest.
      await update($, watch, w => ({
        ...w, rows: [], state: '', head: '', active: false,
        status: 'stopped from the web page; /chest starts it again',
      }))
    } else {
      await update($, watch, w => ({
        ...w, rows: [], state: '', head: '',
        status: `watcher exited (${why})${err ? ': ' + err : ''}. /chest restarts it`,
      }))
    }
  } catch (err) {
    await update($, watch, w => ({ ...w, rows: [], state: '', head: '', status: `watcher failed: ${String(err)}` }))
  } finally {
    tick?.cancel()
    running = false
    child = undefined
    stopping = false
    token = ''
    lastHit = ''            // a new watcher counts loads from #1 again
    if (startAgain) {
      // /chest arrived while the watcher was stopping: bring it back now that it's gone. start() first,
      // before any await, so `running` never reads false in between (send() waits on it).
      startAgain = false
      void start($)
      await update($, watch, w => ({ ...w, active: true }))
    }
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'chest',
      description: 'Open the HoD boss-chest pane; "/chest want Item, Gamertag: Item" sets the desired drop (also: wants, wanted, desire, desired), "/chest maxluck N" and "/chest maxacc A/B" set limits',
    })
    // A reload killed the old module's watcher; bring it back if /chest had started one.
    if ((await read($, watch)).active) {
      await setStatus($, 'mod reloaded, restarting watcher')
      void start($)
    }
    return next(e)
  })

  on('command.run', { command: 'chest' }, async ($, e) => {
    // "/chest want: Item" works too: an optional colon after the word
    const want = /^\s*(?:wants?|wanted|desires?|desired)\b\s*:?(.*)$/i.exec(e.args)
    const cap = /^\s*maxluck\b\s*[:=]?(.*)$/i.exec(e.args)
    const acc = /^\s*maxacc\b\s*[:=]?(.*)$/i.exec(e.args)
    await update($, watch, w => ({ ...w, active: true }))
    const opened = await $.ui.open({ id: PANE, title: TITLE })
    if (running && child && (await codeChanged($))) {
      await setStatus($, 'watcher code changed, restarting')
      // However long the old one takes to exit, start() brings the new one up once it's gone.
      startAgain = true
      token = ''            // so a change sent now waits for the new watcher, not the old one
      // Leaving the stream kills the child; the start() loop then ends and clears `running`.
      // Not awaited: a pull already waiting on the child may hold the return until the child exits.
      const old = child
      child = undefined     // its last records are no longer this sidebar's watcher
      void old.return({} as ProcessSpawnResult).catch(() => undefined)
    }
    void start($)
    const said = want
      ? await setWants($, want[1] ?? '')
      : cap
        ? await setCap($, cap[1] ?? '')
        : acc
          ? await setAcc($, acc[1] ?? '')
          : 'Boss chest pane opened.'
    const w = await read($, watch)
    const age = w.at ? `${Math.round((Date.now() - w.at) / 1000)}s ago` : 'none yet'
    const pane = opened.isPlaced ? 'pane drawn' : `pane NOT drawn: ${opened.reason}`
    return { text: `${said} Web table: ${URL} [${pane}; watcher ${w.status}; last report ${age}; ${w.rows.length} player(s)]` }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    const w = await read($, watch)
    const clock = (ms: number) => (ms ? new Date(ms).toLocaleTimeString() : 'never')
    const stale = running && w.at > 0 && Date.now() - w.at > STALE_MS   // alive, but not reporting
    const rows = stale ? [] : w.rows    // a stalled watcher's last verdict (a YES, maybe) no longer holds
    const live = running && !stale && (w.state === 'live' || w.state === 'no chest')
    const why = !running
      ? `watcher not running (${w.status})`
      : stale
        ? `the watcher stopped reporting (last ${clock(w.at)}); /chest restarts it if it exits`
      : w.state === 'waiting'
        ? w.note
        : w.state
          ? w.note || w.state
          : w.status || 'starting the watcher'     // starting, or just exited (the status says which)
    const cols: [keyof Row, string, string][] = [
      ['player', 'Player', '15%'],
      ['char', 'Character', '13%'],
      ['luck', 'Luck', '8%'],
      ['want', 'Desired drop', '16%'],
      ['manip', 'Can manipulate', '26%'],
      ['drop', 'Expected drop', '22%'],
    ]
    const manipColor = (t: string) =>
      t.startsWith('YES') ? 'green' : t.startsWith('yes') ? 'cyan' : t.startsWith('maybe') ? 'yellow' : undefined
    // Same matching as chest_watch.py's squash(): no case, spaces, apostrophes or possessive 's.
    const squash = (t: string) => t.toLowerCase().replace(/['\u2019]s/g, '').replace(/['\u2019\s]+/g, '')
    // As chest_watch.py's matches(): a substring, or with a leading '=' (a list pick) that exact item.
    const matches = (target: string, name: string) => {
      // both forms of a possessive, as chest_watch.py's forms(): without the 's, and with the s kept
      const forms = (t: string) => [squash(t), t.toLowerCase().replace(/['\u2019\s]+/g, '')]
      const exact = squash(target).startsWith('=')
      const ts = forms(target).map(t => t.replace(/^=+/, '')).filter(Boolean)
      const ns = forms(name).filter(Boolean)
      return ts.some(t => ns.some(n => (exact ? t === n : n.includes(t))))
    }
    const isWanted = (r: Row, item: string) => (r.targets ?? []).some(t => matches(t, item))

    return (
      <Box flexDirection="column">
        {live ? (
          <Text bold color="green">● Live{w.state === 'no chest' ? ': no boss chest in this stage yet' : ''}</Text>
        ) : (
          <Text bold color="red">○ Not live: {why}</Text>
        )}
        <Text dimColor>
          Updated {clock(w.changed)}
          {w.at ? ` (checked ${clock(w.at)})` : ''} · {URL}
        </Text>
        {w.warn ? <Text color="yellow">{w.warn}</Text> : null}
        <Text> </Text>
        <Box flexDirection="row">
          {cols.map(([, label, width]) => (
            <Box width={width} paddingRight={1}>
              <Text bold underline>{label}</Text>
            </Box>
          ))}
        </Box>
        {rows.length === 0 && <Text dimColor>No players yet. They show up once you're in a stage with a boss chest.</Text>}
        {rows.map(r => (
          <Box flexDirection="row">
            {cols.map(([key, , width]) => (
              <Box width={width} paddingRight={1}>
                {key === 'manip' ? (
                  <Text color={manipColor(r.manip)} bold={r.manip.startsWith('YES')}>{r.manip}</Text>
                ) : key === 'drop' ? (
                  <Text bold color={!r.drop.startsWith('by ') && isWanted(r, r.drop) ? 'green' : undefined}>{r.drop}</Text>
                ) : key === 'want' && r.want === 'None' ? (
                  <Text dimColor>None</Text>
                ) : (
                  <Text>{String(r[key])}</Text>
                )}
              </Box>
            ))}
          </Box>
        ))}
        {rows.filter(r => r.odds.length).map(r => (
          <Box flexDirection="column" marginTop={1}>
            <Text bold>
              Drop list: {r.player} ({r.char})
            </Text>
            <Text wrap="wrap">
              {r.odds.slice(0, 12).map(([item, pct], i) => (
                <Text color={isWanted(r, item) ? 'green' : undefined} bold={item === r.drop}>
                  {i ? ', ' : ''}
                  {item} {pct}%
                </Text>
              ))}
              {r.odds.length > 12 ? <Text dimColor>, +{r.odds.length - 12} more</Text> : ''}
            </Text>
          </Box>
        ))}
      </Box>
    )
  })
}
