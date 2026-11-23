export type Watch = {
  /** The latest full frame chest_watch.py printed, timestamp line first. */
  frame: string
  /** One line about the watcher itself: starting, running, exited, errors. */
  status: string
  /** Whether /chest started the watcher in this session, so a mod reload restarts it. */
  active: boolean
}

declare module 'claude-code' {
  interface PluginState {
    'hod-chest': { watch: Shaped<Watch> }
  }
}
