/** Client-side New York calendar windows for the bars API. */

export type BarPeriod = "today" | "week_to_date" | "month_to_date" | "year_to_date" | "calendar_month";
export interface BarWindow {
  /** Inclusive calendar dates. The API expands a date-only `to` through that day. */
  from: string;
  to: string;
  tz: "America/New_York";
  days: number;
}

const nyParts = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
});
const iso = (date: Date): string => date.toISOString().slice(0, 10);
const day = (year: number, month: number, date: number): Date =>
  new Date(Date.UTC(year, month - 1, date));
const daysBetween = (from: Date, to: Date): number =>
  Math.round((to.getTime() - from.getTime()) / 86_400_000) + 1;

export function resolveBarWindow(
  period: BarPeriod, options: { now?: Date; month?: string } = {},
): BarWindow {
  const now = options.now ?? new Date();
  if (!Number.isFinite(now.getTime())) throw new RangeError("now must be a valid Date");
  const parts = Object.fromEntries(nyParts.formatToParts(now).map(p => [p.type, p.value]));
  const current = day(Number(parts.year), Number(parts.month), Number(parts.day));
  if (period !== "calendar_month" && options.month !== undefined) {
    throw new RangeError("month applies only to calendar_month");
  }
  let start: Date;
  let end = current;
  switch (period) {
    case "today": start = current; break;
    case "week_to_date": {
      const weekdayFromMonday = (current.getUTCDay() + 6) % 7;
      start = day(current.getUTCFullYear(), current.getUTCMonth() + 1,
        current.getUTCDate() - weekdayFromMonday);
      break;
    }
    case "month_to_date":
      start = day(current.getUTCFullYear(), current.getUTCMonth() + 1, 1);
      break;
    case "year_to_date": start = day(current.getUTCFullYear(), 1, 1); break;
    case "calendar_month": {
      if (options.month !== undefined && !/^\d{4}-(0[1-9]|1[0-2])$/.test(options.month)) {
        throw new RangeError("month must be YYYY-MM");
      }
      const year = options.month ? Number(options.month.slice(0, 4)) : current.getUTCFullYear();
      const month = options.month ? Number(options.month.slice(5, 7)) : current.getUTCMonth() + 1;
      start = day(year, month, 1);
      end = day(year, month + 1, 0);
      break;
    }
    default: throw new RangeError(`unknown bar period ${String(period)}`);
  }
  return { from: iso(start), to: iso(end), tz: "America/New_York",
    days: daysBetween(start, end) };
}

const maxDays: Record<string, number> = {
  "1s": 7, "1m": 30, "5m": 90, "1h": 365, "1d": 1825,
};

/** Reject a range the backend would silently clamp. Equality is conservative around DST. */
export function validateBarWindow(window: BarWindow, timeframe: string): void {
  const cap = maxDays[timeframe];
  if (cap === undefined) throw new RangeError(`unsupported bars timeframe ${timeframe}`);
  if (window.days >= cap) {
    throw new RangeError(`${timeframe} cannot cover ${window.days} calendar days in one request; `
      + "choose a coarser timeframe or a shorter period");
  }
}
