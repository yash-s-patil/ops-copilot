/** The people and tickets of the demo story, as the fake agent seeds them. */
export const USERS = {
  priya: "Priya Nair",
  rohan: "Rohan Iyer",
  arjun: "Arjun Mehta",
  kavita: "Kavita Rao",
  sam: "Sam Admin",
} as const;
export type User = keyof typeof USERS;

/** West region. `stuck` starts with L1, `lost` with L2 and needs a refund above INR 5,000. */
export const TICKETS = { stuck: 2207, exception: 2188, lost: 2199, resolved: 2150 } as const;
export const ORDERS = { stuck: 4471, lost: 3912 } as const;

export const INVESTIGATE = "Investigate this ticket and propose a resolution.";
export const SMALL_REFUND = /INR\s620\.00/;
export const LARGE_REFUND = /INR\s8,900\.00/;
