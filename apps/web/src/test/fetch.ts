import { vi, type Mock } from "vitest";

export type FetchMock = Mock<typeof fetch>;

export function fakeFetch(): FetchMock {
  const mock = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", mock);
  return mock;
}

type Answer = { status?: number; headers?: Record<string, string> };

/** Every call is answered with a fresh JSON response, since a body can be read only once. */
export function answerJson(mock: FetchMock, body: unknown, answer: Answer = {}): void {
  mock.mockImplementation(() => Promise.resolve(Response.json(body, answer)));
}

export function answerText(mock: FetchMock, body: string, answer: Answer = {}): void {
  mock.mockImplementation(() => Promise.resolve(new Response(body, answer)));
}

export type Sent = {
  url: string;
  method: string | undefined;
  headers: Headers;
  body: string | null;
  init: RequestInit;
};

/** What one call to fetch was given. */
export function sent(mock: FetchMock, index = 0): Sent {
  const call = mock.mock.calls[index];
  if (call === undefined) throw new Error(`fetch was not called ${index + 1} times`);
  const [input, init = {}] = call;
  return {
    url: input instanceof Request ? input.url : String(input),
    method: init.method,
    headers: new Headers(init.headers),
    body: typeof init.body === "string" ? init.body : null,
    init,
  };
}
