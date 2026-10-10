/** The API used by every page: the real backend, or the offline mock when VITE_USE_MOCKS=true. */
import { USE_MOCKS } from "./client";
import { mockApi } from "./mock";
import { realApi } from "./real";
import type { Api } from "./real";

export const api: Api = USE_MOCKS ? mockApi : realApi;
export { ApiError, USE_MOCKS, getActor, setActor } from "./client";
export type { Api, Health, ScenarioResult } from "./real";
