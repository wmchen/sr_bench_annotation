import { describe, expect, it, vi } from "vitest";
import { ApiError, type DatasetStatistics } from "../../api/client";
import { StatisticsResource, type StatisticsState } from "./statisticsResource";

const sample = { dataset: "text", sample_groups: 12 } as DatasetStatistics;
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

describe("statistics request lifecycle", () => {
  it("merges concurrent refreshes and fetches again after a write during an in-flight request", async () => {
    const first = deferred<DatasetStatistics>();
    const fetch = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue({ ...sample, instances: 2 });
    const states: StatisticsState[] = [];
    const resource = new StatisticsResource(fetch, state => states.push(state));
    const pending = resource.refresh();
    await Promise.all([resource.refresh(), resource.refresh(), resource.refresh()]);
    expect(fetch).toHaveBeenCalledTimes(1);
    first.resolve(sample);
    await pending;
    await Promise.resolve();
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(states.at(-1)?.data?.instances).toBe(2);
  });
  it("retains old counts on failure and supports retry without affecting other state", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(sample).mockRejectedValueOnce(new Error("offline")).mockResolvedValue(sample);
    const states: StatisticsState[] = [];
    const resource = new StatisticsResource(fetch, state => states.push(state));
    await resource.refresh();
    await resource.refresh();
    expect(states.at(-1)).toMatchObject({ data: sample, error: "统计更新失败", loading: false });
    await resource.refresh();
    expect(states.at(-1)?.error).toBe("");
  });
  it.each([401, 403])("clears cached data and stops requests after access denial %s", async status => {
    const fetch = vi.fn().mockResolvedValueOnce(sample).mockRejectedValue(new ApiError(status, "unauthorized", "denied"));
    const states: StatisticsState[] = [];
    const resource = new StatisticsResource(fetch, state => states.push(state));
    await resource.refresh();
    await resource.refresh();
    expect(states.at(-1)).toMatchObject({ data: null, denied: true });
    await resource.refresh();
    expect(fetch).toHaveBeenCalledTimes(2);
  });
  it("ignores late responses and queued retries after changing dataset or session", async () => {
    const first = deferred<DatasetStatistics>();
    const fetch = vi.fn().mockReturnValue(first.promise), publish = vi.fn();
    const resource = new StatisticsResource(fetch, publish);
    const pending = resource.refresh();
    await resource.refresh();
    resource.dispose();
    publish.mockClear();
    first.resolve(sample);
    await pending;
    expect(publish).not.toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
