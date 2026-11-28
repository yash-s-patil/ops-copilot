const SESSION = "authjs.session-token";
const GONE = /;\s*max-age=0(?:;|$)/i;

function nameAndValue(pair: string): [string, string] | null {
  const at = pair.indexOf("=");
  return at <= 0 ? null : [pair.slice(0, at).trim(), pair.slice(at + 1).trim()];
}

/**
 * The `Cookie` header the rest of this request should see once the session was renewed on
 * its way in. Sign-in renews the session in the proxy and puts the new cookie only on the
 * response, so without this a page would still read the token that had just run out.
 * Only the session's own cookies, which may come in numbered pieces, are replaced.
 */
export function withRenewedSession(cookieHeader: string, setCookies: readonly string[]): string {
  const jar = new Map<string, string>();
  for (const pair of cookieHeader.split(";")) {
    const found = nameAndValue(pair);
    if (found !== null) jar.set(...found);
  }
  for (const line of setCookies) {
    const found = nameAndValue(line.split(";")[0] ?? "");
    if (found === null || !found[0].includes(SESSION)) continue;
    const [name, value] = found;
    if (value === "" || GONE.test(line)) jar.delete(name);
    else jar.set(name, value);
  }
  return [...jar].map(([name, value]) => `${name}=${value}`).join("; ");
}
