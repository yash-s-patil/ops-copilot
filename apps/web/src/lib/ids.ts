const RUN_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** A run's id as the agent writes it. Anything else from an address is not put in a request. */
export function isRunId(value: string | undefined): value is string {
  return value !== undefined && RUN_ID.test(value);
}
