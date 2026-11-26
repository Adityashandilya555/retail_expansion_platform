/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Platform API base URL (Railway `api` domain when deployed). */
  readonly VITE_API_BASE_URL?: string;
  /** The client app (zm-web), where Preview (#42) opens. */
  readonly VITE_WEB_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
