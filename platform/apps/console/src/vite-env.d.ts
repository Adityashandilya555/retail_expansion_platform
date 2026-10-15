/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Platform API base URL (Railway `api` domain when deployed). */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
