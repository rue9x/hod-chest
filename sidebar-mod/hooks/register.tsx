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

const watch = atom(
  { plugin: 'hod-chest', key: 'watch' } as const,
  { state: '', note: '', head: '', warn: '', changed: 0, at: 0, rows: [] as Row[], status: 'not started', active: false },
  { shape: 'watch-5' },
)

// Module-local: a reload kills the child with the module, so this starts over too.
// (A reload is also how a changed chest_watch.py gets picked up: session.start restarts it.)
let running = false
// The running watcher, so /chest can stop it, and when it started (ms) to spot code changed since.
let child: HookStream<ProcessSpawnChunk, ProcessSpawnResult> | undefined
let startedAt = 0
const CODE = ['chest_watch.py', 'pool.py', 'gmem.py']

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
async function send($: Api, change: Record<string, string>): Promise<string> {
  let last = ''
  for (let i = 0; i < 20; i++) {   // the watcher may still be starting its server
    try {
      const r = await $.http.fetch(`${URL}set`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Origin: `http://localhost:${PORT}` },
        body: JSON.stringify(change),
      })
      const out = JSON.parse(r.text || '{}') as { said?: string }
      return out.said ?? (r.ok ? 'Saved.' : `The watcher refused it (HTTP ${r.status}).`)
    } catch (err) {
      last = String(err)
      await $.clock.sleep(250)
    }
  }
  return `Couldn't reach the watcher at ${URL}: ${last}`
}

const NOT_SAVED = 'Settings reset when the watcher restarts.'

// `/chest want a, Gamertag: b` sets the whole desired-drop list; `/chest want` clears it.
async function setWants($: Api, args: string): Promise<string> {
  const said = await send($, { field: 'wants', value: args.trim() })
  return said.startsWith('Desired drop:') ? `${said}. Reload the stage until Can manipulate says YES or yes.` : said
}

// `/chest maxluck N` caps the Luck the gear suggestions may ask of you; `/chest maxluck` resets it.
async function setCap($: Api, args: string): Promise<string> {
  const n = args.trim()
  if (n && !/^\d+$/.test(n)) return 'Give the cap as a whole number, like /chest maxluck 80.'
  await send($, { field: 'maxluck', key: 'you', value: n })
  return n ? `Suggestions now stay at or below LCK ${n}. ${NOT_SAVED}` : 'Luck cap reset to your current Luck + 40.'
}

// `/chest maxacc A/B`: how many type-A / type-B luck items you own; `/chest maxacc` resets it.
async function setAcc($: Api, args: string): Promise<string> {
  const v = args.trim()
  if (v && !/^[0-2](\/[0-2])?$/.test(v)) return 'Give it as A/B, each 0-2, like /chest maxacc 1/0.'
  await send($, { field: 'maxacc', key: 'you', value: v })
  return v
    ? `Suggestions now use at most ${v} luck items (type A: Thief Ring, Ebisu Raiment; type B: Soul Eater Ring). ${NOT_SAVED}`
    : 'Luck-item limit reset to what your character can equip.'
}

async function noteHit($: Api, head: string) {
  if (!head.startsWith(HIT) && !head.startsWith(REACH)) return
  if (head === lastHit) return
  lastHit = head
  const text = head.replace(/^\*+ /, '')
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
  if (running) return
  running = true
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
    let buf = ''
    let errTail = ''
    for await (const { stream, text } of proc) {
      if (stream === 'stderr') {
        errTail = (errTail + text).slice(-2000)
        continue
      }
      // One JSON record per line; only the newest whole one matters.
      buf += text
      const lines = buf.split('\n')
      buf = lines.pop() ?? ''
      const last = lines.filter(l => l.trim()).pop()
      if (!last) continue
      let rec: { state: string; note: string; head: string; warn?: string; changed: number; rows: Row[] }
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
        warn: rec.warn ?? '',
        rows: rec.rows,
        changed: Math.round(rec.changed * 1000),
        status: 'running',
        at: Date.now(),
      }))
      await noteHit($, rec.head)
    }
    const end = await proc.result.catch(() => undefined)
    const why = end?.signal ? `signal ${end.signal}` : `code ${end?.code}`
    const err = errTail.trim().split('\n').slice(-3).join(' | ')
    if (errTail.includes('stopped from the web page')) {
      // Stopped on purpose: don't bring it back on a mod reload, only on /chest.
      await update($, watch, w => ({ ...w, active: false, status: 'stopped from the web page; /chest starts it again' }))
    } else {
      await setStatus($, `watcher exited (${why})${err ? ': ' + err : ''}. /chest restarts it`)
    }
  } catch (err) {
    await setStatus($, `watcher failed: ${String(err)}`)
  } finally {
    running = false
    child = undefined
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
    const want = /^\s*(?:wants?|wanted|desires?|desired)\b(.*)$/i.exec(e.args)
    const cap = /^\s*maxluck\b(.*)$/i.exec(e.args)
    const acc = /^\s*maxacc\b(.*)$/i.exec(e.args)
    await update($, watch, w => ({ ...w, active: true }))
    const opened = await $.ui.open({ id: PANE, title: TITLE })
    if (running && child && (await codeChanged($))) {
      await setStatus($, 'watcher code changed, restarting')
      // Leaving the stream kills the child; the start() loop then ends and clears `running`.
      // Not awaited: a pull already waiting on the child may hold the return until the child exits.
      void child.return({} as ProcessSpawnResult).catch(() => undefined)
      for (let i = 0; i < 50 && running; i++) await $.clock.sleep(100)
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
    const live = running && (w.state === 'live' || w.state === 'no chest')
    const why = !running
      ? `watcher not running (${w.status})`
      : w.state === 'waiting'
        ? w.note
        : w.state
          ? w.note || w.state
          : 'starting the watcher'
    const clock = (ms: number) => (ms ? new Date(ms).toLocaleTimeString() : 'never')
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
    const wants = (r: Row) => (r.want === 'None' ? [] : r.want.split(',').map(squash).filter(Boolean))
    const isWanted = (r: Row, item: string) => wants(r).some(x => squash(item).includes(x))

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
        {w.rows.length === 0 && <Text dimColor>No players yet. They show up once you're in a stage with a boss chest.</Text>}
        {w.rows.map(r => (
          <Box flexDirection="row">
            {cols.map(([key, , width]) => (
              <Box width={width} paddingRight={1}>
                {key === 'manip' ? (
                  <Text color={manipColor(r.manip)} bold={r.manip.startsWith('YES')}>{r.manip}</Text>
                ) : key === 'drop' ? (
                  <Text bold color={isWanted(r, r.drop) ? 'green' : undefined}>{r.drop}</Text>
                ) : key === 'want' && r.want === 'None' ? (
                  <Text dimColor>None</Text>
                ) : (
                  <Text>{String(r[key])}</Text>
                )}
              </Box>
            ))}
          </Box>
        ))}
        {w.rows.filter(r => r.odds.length).map(r => (
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
