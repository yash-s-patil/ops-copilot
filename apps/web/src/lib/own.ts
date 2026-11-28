/**
 * A table's own entry for a name that came from a server. A plain lookup would also find
 * what every object inherits, such as `constructor`.
 */
export function own<T>(table: Readonly<Record<string, T>>, name: string): T | undefined {
  return Object.hasOwn(table, name) ? table[name] : undefined;
}
