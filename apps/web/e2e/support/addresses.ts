/** Where the two servers of the end-to-end suite listen. Not 3000: that is the real stack's. */
export const WEB_PORT = 3210;
export const FAKE_AGENT_PORT = 8310;
export const WEB_URL = `http://localhost:${WEB_PORT}`;
export const FAKE_AGENT_URL = `http://127.0.0.1:${FAKE_AGENT_PORT}`;
