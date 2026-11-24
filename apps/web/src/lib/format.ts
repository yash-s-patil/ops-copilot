const MINOR_UNITS_PER_MAJOR = 100;
const LOCALE = "en-IN";

export function formatMoney(amountMinor: number, currency: string): string {
  return new Intl.NumberFormat(LOCALE, {
    style: "currency",
    currency,
    currencyDisplay: "code",
  }).format(amountMinor / MINOR_UNITS_PER_MAJOR);
}
