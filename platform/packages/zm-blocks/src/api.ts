// How blocks reach the platform API (#30): the shell provides its signed-in client around the
// pages it draws, and blocks read data through it with TanStack Query.
import type { ApiClient } from "@zm/api-client";
import { createContext, use } from "react";

export const BlocksApi = createContext<ApiClient | null>(null);

/** The signed-in client; outside the shell it throws, and the block becomes a placeholder. */
export function useBlocksApi(): ApiClient {
  const api = use(BlocksApi);
  if (!api) throw new Error("no API: the shell must wrap its pages in <BlocksApi>");
  return api;
}
