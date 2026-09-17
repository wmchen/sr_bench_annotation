import type { components } from "./generated";

export type DraftRequest = components["schemas"]["DraftRequest"];
export type SaveAnnotationsRequest = components["schemas"]["SaveAnnotationsRequest"];
export type InferenceRequest = components["schemas"]["InferenceRequest"];
export const variants = ["HR", "LR2", "LR3", "LR4"] as const;
export type Variant = typeof variants[number];
export type Point = [number, number];
export type Region = components["schemas"]["RegionView"];
export type Group = components["schemas"]["GroupView"];
export type DatasetStatistics = components["schemas"]["DatasetStatisticsView"];
export type OpeningSelection = components["schemas"]["OpeningSelectionView"];
export type Sample = components["schemas"]["SampleView"];
export type Session = components["schemas"]["SessionView"];
export interface Lease { id: string; generation: string; expires: number }
export interface Dataset {
  id: string; attribute: "text" | "face"; total: number; complete: number;
  status: string; errors: {sample?: string; message: string}[];
}
export interface SampleItem { id: string; complete: boolean; revision: number }
export type Slot = components["schemas"]["SlotView"];
export type ModelInfo = components["schemas"]["ModelView"];
export type Job = components["schemas"]["JobView"];

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) {
    super(message);
  }
}

export async function api<T>(path: string, method = "GET", data?: unknown): Promise<T> {
  const response = await fetch("/api/v1" + path, {
    method, credentials: "same-origin",
    headers: data === undefined ? undefined : { "Content-Type": "application/json" },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  const result = await response.json();
  if (!response.ok) {
    throw new ApiError(response.status, result.error?.code ?? "request",
      result.error?.message ?? "请求失败", result.error?.details);
  }
  return result as T;
}

export const samplePath = (dataset: string, sample: string) =>
  "/datasets/" + encodeURIComponent(dataset) + "/samples/" + encodeURIComponent(sample);
