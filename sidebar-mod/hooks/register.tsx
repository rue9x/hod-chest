import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { ChestView } from '../types'

const PANE = 'chest-pane'
const TITLE = 'Boss chest'
const view = atom({ plugin: 'chest-pane', key: 'view' } as const,
  { text: 'starting…', at: '', isError: false } as ChestView, { shape: 'view-1' })

// Python is `python3` on Linux and usually `py` or `python` on Windows
const PYTHONS = [['python3'], ['py', '-3'], ['python']]
let python: string[] | undefined

// The predictor scripts live one folder up from this mod
async function runPredictor($: { plugin: { root: string }; process: { run: (argv: string[], init?: { cwd?: string; timeoutMs?: number }) => Promise<{ exitCode: number; stdout: string; stderr: string }> } }) {
  const cwd = `${$.plugin.root}/..`
  if (!python) {
    for (const cmd of PYTHONS) {
      try {
        const probe = await $.process.run([...cmd, '-c', 'import sys; print(sys.version_info[0])'], { cwd, timeoutMs: 10000 })
        if (probe.exitCode === 0 && probe.stdout.trim() === '3') { python = cmd; break }
      } catch {
        // not installed under this name; try the next one
      }
    }
  }
  if (python) return $.process.run([...python, 'chest_watch.py', '--once'], { cwd, timeoutMs: 15000 })
  throw new Error('Python 3 not found (tried python3, py -3, python)')
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'chest', description: 'Show the boss chest drop prediction pane' })
    void $.ui.open({ id: PANE, title: TITLE })
    let busy = false
    $.clock.every(1000, async () => {
      if (busy) return
      busy = true
      try {
        const r = await runPredictor($)
        const text = r.exitCode === 0 ? r.stdout.trimEnd() : (r.stderr || r.stdout).trim().split('\n').slice(-3).join('\n')
        await update($, view, () => ({ text, at: new Date().toLocaleTimeString(), isError: r.exitCode !== 0 }))
      } catch (err) {
        await update($, view, () => ({ text: String(err), at: new Date().toLocaleTimeString(), isError: true }))
      } finally {
        busy = false
      }
    })
    return next(e)
  })

  on('command.run', { command: 'chest' }, async $ => {
    await $.ui.open({ id: PANE, title: TITLE })
    return { text: 'Boss chest pane opened.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    const v = await read($, view)
    const lines = v.text.split('\n')
    return (
      <Box flexDirection="column">
        {lines.map(line => {
          const isPick = line.includes('WILL DROP')
          return (
            <Text bold={isPick} color={v.isError ? 'red' : isPick ? 'green' : undefined}
              dimColor={!isPick && line.startsWith('      ')}>
              {isPick ? line.replace('  >>> WILL DROP:', '▶ DROP:') : line}
            </Text>
          )
        })}
        <Text dimColor>updated {v.at || '—'}</Text>
      </Box>
    )
  })
}
