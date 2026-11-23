import { atom, read, update } from 'claude-code'
import type { EngineInterface as Api, Register } from 'claude-code'

const PANE = 'hod-chest'
const TITLE = 'Boss chest'
const CLEAR = '\x1b[2J\x1b[H'
const ANSI = /\x1b\[[0-9;?]*[A-Za-z]|\x07/g
const HIT = '*** TARGET HIT'
const REACH = '+++ TARGET REACHABLE'
const PYTHONS: string[][] = [['python3'], ['py', '-3'], ['python']]

const watch = atom(
  { plugin: 'hod-chest', key: 'watch' } as const,
  { frame: '', status: 'not started', active: false },
  { shape: 'watch-2' },
)

// Module-local: a reload kills the child with the module, so this starts over too.
// (A reload is also how a changed chest_watch.py gets picked up: session.start restarts it.)
let running = false
// The hit line last notified about; it names the load number, so each new hit differs.
let lastHit = ''

function dirOf(path: string): string {
  return path.slice(0, Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\')))
}

// want.txt beside chest_watch.py: one target name per line, plus an optional `maxluck=N` line.
async function wantFile($: Api): Promise<string | undefined> {
  const script = await findScript($)
  return script ? `${dirOf(script)}/want.txt` : undefined
}

async function readWantLines($: Api, path: string): Promise<string[]> {
  try {
    return (await $.fs.read(path)).split('\n').map(l => l.trim()).filter(Boolean)
  } catch {
    return []
  }
}

const isSetting = (line: string) => /^(maxluck|maxacc)=/i.test(line)

// `/chest want a, b` sets the targets; `/chest want` clears them. A Luck cap line is kept.
async function setWants($: Api, args: string): Promise<string> {
  const path = await wantFile($)
  if (!path) return 'chest_watch.py not found, so there is nowhere to save want.txt.'
  const wants = args.split(',').map(s => s.trim()).filter(Boolean)
  const settings = (await readWantLines($, path)).filter(isSetting)
  await $.fs.write(path, [...wants, ...settings].map(l => l + '\n').join(''))
  return wants.length
    ? `Watching for: ${wants.join(', ')}. Reload the stage until the pane says TARGET HIT or REACHABLE.`
    : 'Targets cleared.'
}

// Replaces the `name=` line in want.txt with `name=value`, or removes it when value is empty.
async function setSetting($: Api, name: string, value: string) {
  const path = await wantFile($)
  if (!path) return false
  const keep = (await readWantLines($, path)).filter(l => !l.toLowerCase().startsWith(name + '='))
  await $.fs.write(path, [...keep, ...(value ? [`${name}=${value}`] : [])].map(l => l + '\n').join(''))
  return true
}

// `/chest maxluck N` caps the Luck the gear suggestions may ask for; `/chest maxluck` resets it.
async function setCap($: Api, args: string): Promise<string> {
  const n = args.trim()
  if (n && !/^\d+$/.test(n)) return 'Give the cap as a whole number, like /chest maxluck 80.'
  if (!(await setSetting($, 'maxluck', n))) return 'chest_watch.py not found, so there is nowhere to save want.txt.'
  return n ? `Suggestions now stay at or below LCK ${n}.` : 'Luck cap reset to your current Luck + 40.'
}

// `/chest maxacc A/B`: how many type-A / type-B luck items you own; `/chest maxacc` resets it.
async function setAcc($: Api, args: string): Promise<string> {
  const v = args.trim()
  if (v && !/^[0-2](\/[0-2])?$/.test(v)) return 'Give it as A/B, each 0-2, like /chest maxacc 1/0.'
  if (!(await setSetting($, 'maxacc', v))) return 'chest_watch.py not found, so there is nowhere to save want.txt.'
  return v
    ? `Suggestions now use at most ${v} luck items (type A: Thief Ring, Ebisu Raiment; type B: Soul Eater Ring).`
    : 'Luck-item limit reset to what your character can equip.'
}

async function noteHit($: Api, frame: string) {
  const head = frame.split('\n')[0] ?? ''
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
    const child = $.process.spawn({
      argv: [...py, '-u', script],
      cwd: dir,
      env: { PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
    })
    let buf = ''
    let errTail = ''
    for await (const { stream, text } of child) {
      if (stream === 'stderr') {
        errTail = (errTail + text).slice(-2000)
        continue
      }
      buf += text
      const parts = buf.split(CLEAR)
      // Keep only the newest frame; it is whole once it ends in a newline.
      buf = parts[parts.length - 1] ?? ''
      const done = buf.endsWith('\n') ? buf : (parts[parts.length - 2] ?? '')
      if (done.trim()) {
        const frame = done.replace(ANSI, '').replace(/\s+$/, '')
        await update($, watch, w => ({ ...w, frame, status: 'live' }))
        await noteHit($, frame)
      }
    }
    const end = await child.result
    const why = end?.signal ? `signal ${end.signal}` : `code ${end?.code}`
    const err = errTail.trim().split('\n').slice(-3).join(' | ')
    await setStatus($, `watcher exited (${why})${err ? ': ' + err : ''}. /chest restarts it`)
  } catch (err) {
    await setStatus($, `watcher failed: ${String(err)}`)
  } finally {
    running = false
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'chest',
      description: 'Open the HoD boss-chest pane; "/chest want Item, Item" sets targets, "/chest maxluck N" and "/chest maxacc A/B" set limits',
    })
    // A reload killed the old module's watcher; bring it back if /chest had started one.
    if ((await read($, watch)).active) {
      await setStatus($, 'mod reloaded, restarting watcher')
      void start($)
    }
    return next(e)
  })

  on('command.run', { command: 'chest' }, async ($, e) => {
    const want = /^\s*want\b(.*)$/i.exec(e.args)
    const cap = /^\s*maxluck\b(.*)$/i.exec(e.args)
    const acc = /^\s*maxacc\b(.*)$/i.exec(e.args)
    const said = want
      ? await setWants($, want[1] ?? '')
      : cap
        ? await setCap($, cap[1] ?? '')
        : acc
          ? await setAcc($, acc[1] ?? '')
          : 'Boss chest pane opened.'
    await update($, watch, w => ({ ...w, active: true }))
    await $.ui.open({ id: PANE, title: TITLE })
    void start($)
    return { text: said }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    const { frame, status } = await read($, watch)
    const lines = frame ? frame.split('\n') : []
    const room = Math.max(1, (e.viewport?.rows ?? 24) - 3)

    return (
      <Box flexDirection="column">
        {lines.length === 0 && <Text dimColor>Waiting for chest_watch.py…</Text>}
        {lines.slice(0, room).map(line =>
          line.startsWith(HIT) ? (
            <Text bold inverse color="green">{line}</Text>
          ) : line.startsWith(REACH) ? (
            <Text bold color="cyan">{line}</Text>
          ) : line.startsWith('???') ? (
            <Text bold color="yellow">{line}</Text>
          ) : line.startsWith('---') ? (
            <Text dimColor>{line}</Text>
          ) : line.includes('WILL DROP') ? (
            <Text bold color="green">{line}</Text>
          ) : (
            <Text>{line || ' '}</Text>
          ),
        )}
        <Text dimColor>{status}</Text>
      </Box>
    )
  })
}
