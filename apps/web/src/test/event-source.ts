import { vi } from "vitest";

type Listener = (message: MessageEvent<string>) => void;

/** Stands in for the browser's event source: the test plays the server's part. */
export class FakeEventSource {
  static opened: FakeEventSource[] = [];

  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  private readonly listeners = new Map<string, Listener[]>();

  constructor(readonly url: string) {
    FakeEventSource.opened.push(this);
  }

  addEventListener(type: string, listener: Listener): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close(): void {
    this.closed = true;
  }

  /** One event as the server writes it: its number in the id line, its payload as JSON text. */
  send(seq: number | string, type: string, data: unknown = {}): void {
    this.sendRaw(String(seq), type, JSON.stringify(data));
  }

  sendRaw(lastEventId: string, type: string, data: string): void {
    const message = new MessageEvent<string>(type, { data, lastEventId });
    for (const listener of this.listeners.get(type) ?? []) listener(message);
  }

  listensFor(type: string): boolean {
    return this.listeners.has(type);
  }
}

/** Replaces the browser's event source. Gives the one the code under test opened last. */
export function fakeEventSource(): () => FakeEventSource {
  FakeEventSource.opened = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  return () => {
    const last = FakeEventSource.opened.at(-1);
    if (last === undefined) throw new Error("no event source was opened");
    return last;
  };
}
