import { ApiError, type DatasetStatistics } from "../../api/client";

export interface StatisticsState {
  data: DatasetStatistics | null;
  loading: boolean;
  error: string;
  denied: boolean;
}
export const initialStatistics: StatisticsState = { data: null, loading: true, error: "", denied: false };

/** Coalesce invalidations and fence replies from disposed sessions/datasets. */
export class StatisticsResource {
  private active = true;
  private busy = false;
  private queued = false;
  private state: StatisticsState = { ...initialStatistics };

  constructor(private fetch: () => Promise<DatasetStatistics>, private publish: (state: StatisticsState) => void) {}

  refresh = async (): Promise<void> => {
    if (!this.active || this.state.denied) return;
    if (this.busy) { this.queued = true; return; }
    this.busy = true;
    this.update({ ...this.state, loading: true });
    try {
      const data = await this.fetch();
      this.update({ data, loading: false, error: "", denied: false });
    } catch (error) {
      const denied = error instanceof ApiError && [401, 403].includes(error.status);
      this.update({ data: denied ? null : this.state.data, loading: false, denied,
        error: denied ? "统计访问权限已失效" : "统计更新失败" });
    } finally {
      this.busy = false;
      if (this.queued) { this.queued = false; void this.refresh(); }
    }
  };

  dispose(): void { this.active = false; this.queued = false; }

  private update(state: StatisticsState): void {
    if (!this.active) return;
    this.state = state;
    this.publish(state);
  }
}
