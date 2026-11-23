export type ChestView = { text: string; at: string; isError: boolean }

declare module 'claude-code' {
  interface PluginState {
    'chest-pane': { view: Shaped<ChestView> }
  }
}
