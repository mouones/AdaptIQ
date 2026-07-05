/** Frontend service helpers for dashboardEvents behavior. */

export const DASHBOARD_STATS_UPDATED_EVENT = 'adaptiq:dashboard-stats-updated';

export function notifyDashboardStatsUpdated(): void {
  if (typeof window === 'undefined') {
    return;
  }

  window.dispatchEvent(new Event(DASHBOARD_STATS_UPDATED_EVENT));
}