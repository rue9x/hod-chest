/** One player's line in the pane's table (chest_watch.py --json). */
export type Row = {
  /** How the watcher addresses this player in POST /set: 'you', a gamertag, or friendN. */
  key: string
  /** Every target prefix that means this player: 'you', character names, gamertag. */
  labels: string[]
  /** What the page has set for this player: maxluck, maxacc, owns, base, luck. */
  settings: Record<string, string>
  player: string
  char: string
  luck: string
  /** Their targets as shown, or "None". */
  want: string
  /** Their targets as matched: a substring, or '=Name' for that exact item (a pick from the list). */
  targets?: string[]
  /** "YES: open it now", "yes: <gear setup>", "maybe ...", "no", or "-" with no target. */
  manip: string
  /** The drop expected at their current Luck. */
  drop: string
  /** [item name, % chance] for their chest at their Luck, likeliest first; empty when their Luck is unknown. */
  odds: [string, number][]
  /** Every item their chest can give, by name: the page's desired-drop choices. */
  items: string[]
}

export type Watch = {
  /** live (a boss chest), "no chest" (in the game, no boss chest), or "waiting" (game not found). */
  state: string
  /** Why it isn't live, from the watcher. */
  note: string
  /** The TARGET HIT / REACHABLE line, for the toast. */
  head: string
  /** Targets no item name matches, and why the web page isn't served, joined; '' when neither. */
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
