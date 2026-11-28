import { test as base } from "@playwright/test";
import { FakeAgent } from "./fake";

/** Every test starts from the same story: the fake agent is put back before each one. */
export const test = base.extend<{ fake: FakeAgent }>({
  fake: [
    async ({ request }, use) => {
      const fake = new FakeAgent(request);
      await fake.reset();
      await use(fake);
    },
    { auto: true },
  ],
});

export { expect } from "@playwright/test";
