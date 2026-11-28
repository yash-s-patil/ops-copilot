const MINOR_UNITS_PER_MAJOR = 100;
const LOCALE = "en-IN";
const MS_PER_S = 1000;
const S_PER_MIN = 60;
const MIN_PER_H = 60;
const H_PER_DAY = 24;
const BYTES_PER_KB = 1000;

export function formatMoney(amountMinor: number, currency: string): string {
  return new Intl.NumberFormat(LOCALE, {
    style: "currency",
    currency,
    currencyDisplay: "code",
  }).format(amountMinor / MINOR_UNITS_PER_MAJOR);
}

export function formatCount(value: number): string {
  return new Intl.NumberFormat(LOCALE).format(Math.round(value));
}

/** How long ago, in the one unit that matters: "4 d", "3 h", "12 min", "now". */
export function formatAge(since: Date, now: Date): string {
  const minutes = Math.floor((now.getTime() - since.getTime()) / MS_PER_S / S_PER_MIN);
  if (minutes < 1) return "now";
  if (minutes < MIN_PER_H) return `${minutes} min`;
  const hours = Math.floor(minutes / MIN_PER_H);
  return hours < H_PER_DAY ? `${hours} h` : `${Math.floor(hours / H_PER_DAY)} d`;
}

export function formatSeconds(ms: number): string {
  return `${(ms / MS_PER_S).toFixed(1)} s`;
}

export function formatBytes(bytes: number): string {
  return bytes >= BYTES_PER_KB ? `${(bytes / BYTES_PER_KB).toFixed(1)} kB` : `${bytes} B`;
}

/** The time of day in the viewer's zone. The full UTC time goes in a tooltip. */
export function formatClock(at: Date): string {
  return new Intl.DateTimeFormat(LOCALE, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(at);
}

export function formatUtc(at: Date): string {
  return `${at.toISOString().replace("T", " ").slice(0, 16)} UTC`;
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join("");
}

export function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

export function sentenceCase(text: string): string {
  const spaced = text.replaceAll("_", " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
