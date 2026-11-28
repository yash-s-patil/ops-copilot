"use client";

import * as Menu from "@radix-ui/react-dropdown-menu";
import { onlineManager, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { Activity, Check, Inbox, Layers, List, MessageSquare, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { ticketsKey } from "@/components/inbox/ticket-list";
import { Switch } from "@/components/ui/switch";
import { Tooltip } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { initials } from "@/lib/format";
import { ADMIN_ROLE } from "@/lib/roles";
import { regionLabel, roleLabel, useSession } from "@/lib/session";
import { setTheme, themeFrom, THEMES, type Theme } from "@/lib/theme";

const ICON = { size: 16, strokeWidth: 1.75, "aria-hidden": true } as const;

type Item = { href: string; label: string; icon: LucideIcon; count?: number };

const ADMIN_ITEMS: Item[] = [
  { href: "/admin/live", label: "Live trace", icon: Activity },
  { href: "/admin/runs", label: "Runs", icon: List },
];

function Brand() {
  return (
    <div className="flex items-center gap-[9px] px-1 py-0.5 max-lg:justify-center">
      <span
        aria-hidden="true"
        className="grid size-[26px] shrink-0 place-items-center rounded-[7px] bg-ink text-surface"
      >
        <Layers {...ICON} />
      </span>
      <div className="max-lg:hidden">
        <b className="block font-semibold tracking-[-0.01em]">Ops Copilot</b>
        <span className="block text-fine text-ink-3">Meridian Retail</span>
      </div>
    </div>
  );
}

function NavLink({ item, current }: { item: Item; current: boolean }) {
  const Icon = item.icon;
  const state = current
    ? "bg-surface font-medium text-ink ring-1 ring-line"
    : "text-ink-2 hover:bg-line";
  return (
    <Tooltip label={item.label}>
      <Link
        href={item.href}
        aria-current={current ? "page" : undefined}
        className={`flex h-8 shrink-0 items-center gap-[9px] rounded-control px-2 max-lg:justify-center max-lg:px-0 max-sm:px-[9px] pointer-coarse:min-h-11 ${state}`}
      >
        <Icon {...ICON} />
        <span className="max-lg:sr-only max-sm:not-sr-only">{item.label}</span>
        {item.count !== undefined && (
          <span className="ml-auto text-fine text-ink-3 tabular-nums max-lg:hidden">
            {item.count}
          </span>
        )}
      </Link>
    </Tooltip>
  );
}

function useItems(): { items: Item[]; heading: string | null } {
  const { me, limits } = useSession();
  const admin = me.roles.includes(ADMIN_ROLE);
  const queue = useQuery({
    queryKey: ticketsKey("my_queue"),
    queryFn: () => api.tickets("my_queue"),
    refetchInterval: limits.inboxRefreshMs,
    enabled: !admin,
  });
  if (admin) return { items: ADMIN_ITEMS, heading: "Operations" };
  return {
    heading: null,
    items: [
      { href: "/inbox", label: "Inbox", icon: Inbox, count: queue.data?.counts.my_queue },
      { href: "/ask", label: "Ask Copilot", icon: MessageSquare },
    ],
  };
}

function TechnicalSwitch() {
  const { me, technical, setTechnical } = useSession();
  if (!me.demo_tools || me.roles.includes(ADMIN_ROLE)) return null;
  return (
    <div className="flex items-center gap-2 px-1 py-0.5 text-ink-2 max-lg:justify-center">
      <Switch checked={technical} onCheckedChange={setTechnical} label="Technical details" />
      <span aria-hidden="true" className="max-lg:hidden">
        Technical details
      </span>
    </div>
  );
}

const THEME_LABELS: Record<Theme, string> = {
  system: "Match the system",
  light: "Light",
  dark: "Dark",
};
const MENU_ITEM =
  "flex h-[30px] cursor-pointer items-center rounded-control px-2 outline-none data-[highlighted]:bg-raised";

/** Drawn only while the menu is open, so it can read the theme the page is in. */
function ThemeChoice() {
  const [chosen, setChosen] = useState(() => themeFrom(document.documentElement.dataset["theme"]));
  const choose = (value: string) => {
    const theme = themeFrom(value);
    setChosen(theme);
    setTheme(theme);
  };
  return (
    <Menu.RadioGroup value={chosen} onValueChange={choose}>
      {THEMES.map((theme) => (
        <Menu.RadioItem key={theme} value={theme} className={`${MENU_ITEM} justify-between gap-3`}>
          {THEME_LABELS[theme]}
          <Menu.ItemIndicator>
            <Check size={14} strokeWidth={1.75} aria-hidden="true" />
          </Menu.ItemIndicator>
        </Menu.RadioItem>
      ))}
    </Menu.RadioGroup>
  );
}

const DRAIN_STEP_MS = 50;

/**
 * Stops every read and waits for those already on their way before the session ends. An
 * answer that arrived after signing out would carry the session cookie again, and the
 * person would still be signed in.
 */
async function leave(client: QueryClient, signOut: () => void): Promise<void> {
  onlineManager.setOnline(false);
  while (client.isFetching() > 0 || client.isMutating() > 0) {
    await new Promise((settle) => setTimeout(settle, DRAIN_STEP_MS));
  }
  signOut();
}

function UserBlock({ signOut }: { signOut: () => void }) {
  const client = useQueryClient();
  const { me } = useSession();
  const region = regionLabel(me);
  return (
    <Menu.Root>
      <Menu.Trigger className="flex items-center gap-[9px] rounded-control px-1 py-0.5 text-left hover:bg-line max-lg:justify-center">
        <span
          aria-hidden="true"
          className="grid size-[26px] shrink-0 place-items-center rounded-full bg-accent-wash text-label font-semibold text-accent"
        >
          {initials(me.name)}
        </span>
        <span className="min-w-0 max-lg:sr-only">
          <b className="block truncate font-medium">{me.name}</b>
          <span className="block truncate text-fine text-ink-3">
            {roleLabel(me)}
            {region === null ? "" : ` · ${region}`}
          </span>
        </span>
        <span className="sr-only">, theme and sign out</span>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          side="top"
          align="start"
          sideOffset={6}
          className="z-20 min-w-[180px] rounded-card border border-line bg-surface p-1"
        >
          <Menu.Label className="px-2 py-1 text-label font-medium tracking-[0.04em] text-ink-3 uppercase">
            Theme
          </Menu.Label>
          <ThemeChoice />
          <Menu.Separator className="my-1 h-px bg-line" />
          <Menu.Item className={MENU_ITEM} onSelect={() => void leave(client, signOut)}>
            Sign out
          </Menu.Item>
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}

/** A column at desktop widths, icons below 1280, and a bar across the top below 720. */
export function Rail({ signOut }: { signOut: () => void }) {
  const pathname = usePathname();
  const { items, heading } = useItems();
  return (
    <aside className="flex min-h-0 shrink-0 flex-col gap-3.5 border-line bg-rail max-sm:flex-row max-sm:items-center max-sm:gap-2 max-sm:border-b max-sm:py-2 max-sm:safe-x sm:w-16 sm:border-r sm:p-2 sm:pt-3 lg:w-[232px] lg:py-3 lg:pr-2.5 lg:pl-4">
      <Brand />
      <nav
        aria-label="Main"
        className="flex min-h-0 flex-1 flex-col gap-px overflow-y-auto max-sm:flex-row max-sm:gap-0.5 max-sm:overflow-x-auto max-sm:overflow-y-hidden"
      >
        {heading !== null && (
          <h3 className="px-2 pt-2.5 pb-1 text-label font-medium tracking-[0.04em] text-ink-3 uppercase max-lg:hidden">
            {heading}
          </h3>
        )}
        {items.map((item) => (
          <NavLink key={item.href} item={item} current={pathname.startsWith(item.href)} />
        ))}
      </nav>
      <div className="flex flex-col gap-2 border-line max-sm:flex-row max-sm:items-center sm:border-t sm:pt-2.5">
        <TechnicalSwitch />
        <UserBlock signOut={signOut} />
      </div>
    </aside>
  );
}
