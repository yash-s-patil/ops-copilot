import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";
import type { ReactElement, ReactNode } from "react";
import { Switch } from "@/components/ui/switch";
import type { Me } from "@/lib/schemas";
import { SessionProvider, useSession } from "@/lib/session";
import { LIMITS, PRIYA } from "@/test/fixtures";

const TECHNICAL = "Technical details";

function TechnicalSwitch() {
  const { technical, setTechnical } = useSession();
  return <Switch checked={technical} onCheckedChange={setTechnical} label={TECHNICAL} />;
}

export function newClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

type ProvidersProps = { me: Me; client: QueryClient; children: ReactNode };

/** The real session and query providers, with the rail's switch beside what is tested. */
export function Providers({ me, client, children }: ProvidersProps) {
  return (
    <QueryClientProvider client={client}>
      <SessionProvider me={me} limits={LIMITS}>
        <TechnicalSwitch />
        <div data-testid="subject">{children}</div>
      </SessionProvider>
    </QueryClientProvider>
  );
}

type Rendered = { subject: HTMLElement; user: UserEvent; client: QueryClient };

/** Draws a component as one person sees it. `subject` holds the component and nothing else. */
export function renderAs(ui: ReactElement, me: Me = PRIYA): Rendered {
  const client = newClient();
  const user = userEvent.setup();
  const { container } = render(
    <Providers me={me} client={client}>
      {ui}
    </Providers>,
  );
  return { subject: within(container).getByTestId("subject"), user, client };
}

export async function switchTechnicalOn(user: UserEvent): Promise<void> {
  await user.click(screen.getByRole("switch", { name: TECHNICAL }));
}
