import { describe, expect, it } from "vitest";
import { GET } from "@/app/healthz/route";

describe("the health check", () => {
  it("says only that the server answers, and is never cached", async () => {
    const response = GET();
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({ status: "ok" });
  });
});
