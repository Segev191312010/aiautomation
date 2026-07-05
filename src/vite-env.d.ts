/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_JWT_BOOTSTRAP_SECRET?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
