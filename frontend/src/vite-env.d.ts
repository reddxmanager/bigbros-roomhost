/// <reference types="vite/client" />

interface ImportMetaEnv {
  // Backend origin (e.g. https://your-app.onrender.com). Empty in dev so paths
  // stay relative and the Vite dev proxy handles them.
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
