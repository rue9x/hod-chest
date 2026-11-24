/** One player's line in the pane's table (chest_watch.py --json). */
export type Row = {
  player: string
  char: string
  luck: string
  /** Their targets, or "None". */
  want: string
  /** "YES: open it now", "yes: <gear setup>", "maybe ...", "no", or "-" with no target. */
  manip: string
  /** The drop expected at their current Luck. */
  drop: string
  /** [item name, % chance] for their chest, likeliest first. */
  odds: [string, number][]
}

export type Watch = {
  /** live (a boss chest), "no chest" (in the game, no boss chest), or "waiting" (game not found). */
  state: string
  /** Why it isn't live, from the watcher. */
  note: string
  /** The TARGET HIT / REACHABLE line, for the toast. */
  head: string
  /** Targets no item name matches, or ''. */
  warn: string
  /** When the rows last changed (ms since the epoch), 0 before any. */
  changed: number
  /** When the watcher last reported (ms since the epoch), 0 before any. */
  at: number
  rows: Row[]
  /** One line about the watcher process itself: starting, running, exited, errors. */
  status: string
  /** Whether /chest started the watcher in this session, so a mod reload restarts it. */
  active: boolean
}

declare module 'claude-code' {
  interface PluginState {
    'hod-chest': { watch: Shaped<Watch> }
  }
}
