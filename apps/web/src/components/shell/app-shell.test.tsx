// @vitest-environment jsdom
import { useMutation, useQuery } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "@/components/shell/app-shell";
import { api } from "@/lib/api";
import { internalWords } from "@/test/axe";
import { fakeFetch, type FetchMock } from "@/test/fetch";
import { ARJUN, LIMITS, RUN_ID } from "@/test/fixtures";

const router = vi.hoisted(() => ({ push: vi.fn<(href: string) => void>() }));

vi.mock("next/navigation", () => ({
  useRouter: () => router,
  usePathname: () => "/inbox",
}));

const QUEUE = {
  items: [],
  next_cursor: null,
  counts: { my_queue: 2, all_open: 5, waiting: 0, done: 9 },
};
const SIGNED_OUT = { code: "SIGNED_OUT", message: "Sign in again to continue." };

let fetchMock: FetchMock;
/** The status each address answers with; anything not named answers 200. */
let statusOf: Record<string, number>;

beforeEach(() => {
  router.push.mockReset();
  statusOf = {};
  fetchMock = fakeFetch();
  fetchMock.mockImplementation((input) => {
    const path = String(input).replace("/api/bff", "");
    const status = statusOf[path] ?? 200;
    if (status !== 200) return Promise.resolve(Response.json(SIGNED_OUT, { status }));
    return Promise.resolve(Response.json(path.startsWith("/tickets") ? QUEUE : ARJUN));
  });
});

/** A page inside the shell that reads once and can make one change. */
function Page() {
  const who = useQuery({ queryKey: ["me"], queryFn: api.me });
  const cancel = useMutation({ mutationFn: () => api.cancel(RUN_ID) });
  return (
    <div>
      <p>{who.data?.name ?? "reading"}</p>
      <button type="button" onClick={() => cancel.mutate()}>
        Stop the run
      </button>
    </div>
  );
}

function shell() {
  const signOut = vi.fn<() => void>();
  render(
    <AppShell me={ARJUN} limits={LIMITS} signOut={signOut}>
      <Page />
    </AppShell>,
  );
  return { user: userEvent.setup(), signOut };
}

const dialog = () => screen.queryByRole("dialog", { name: "Sign in again" });

describe("the shell while the sign-in is good", () => {
  it("draws the rail and the page, and no dialog", async () => {
    shell();
    expect(await screen.findByText(ARJUN.name, { selector: "p" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Inbox/ }).getAttribute("aria-current")).toBe("page");
    expect(dialog()).toBeNull();
  });

  it("does not take a refusal of another kind for the end of the sign-in", async () => {
    statusOf = { "/me": 403, "/tickets?view=my_queue": 404 };
    shell();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await screen.findByText("reading");
    expect(dialog()).toBeNull();
  });
});

describe("the shell when the sign-in has ended", () => {
  it("asks to sign in again when a read is refused with a 401, and says nothing was lost", async () => {
    statusOf = { "/me": 401 };
    shell();
    const asked = await screen.findByRole("dialog", { name: "Sign in again" });
    expect(asked.textContent).toContain("Your sign-in has ended. Nothing was lost");
    expect(internalWords(asked)).toEqual([]);
  });

  it("does not ask the refused read again", async () => {
    statusOf = { "/me": 401 };
    shell();
    await screen.findByRole("dialog");
    const asked = fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/me"));
    expect(asked).toHaveLength(1);
  });

  it("asks to sign in again when a change is refused with a 401", async () => {
    statusOf = { [`/runs/${RUN_ID}/cancel`]: 401 };
    const { user } = shell();
    await screen.findByText(ARJUN.name, { selector: "p" });
    expect(dialog()).toBeNull();
    await user.click(screen.getByRole("button", { name: "Stop the run" }));
    expect(await screen.findByRole("dialog", { name: "Sign in again" })).toBeTruthy();
  });

  it("goes to the sign-in page from the dialog's button", async () => {
    statusOf = { "/me": 401 };
    const { user } = shell();
    await screen.findByRole("dialog");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(router.push).toHaveBeenCalledWith("/sign-in");
  });

  it("cannot be dismissed with Escape", async () => {
    statusOf = { "/me": 401 };
    const { user } = shell();
    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");
    expect(dialog()).not.toBeNull();
  });
});
