/// <reference types="vite/client" />

import type { FeedgrabIpcApi } from "../../electron/ipc-types";

declare global {
  interface Window {
    feedgrab?: FeedgrabIpcApi;
  }
}
