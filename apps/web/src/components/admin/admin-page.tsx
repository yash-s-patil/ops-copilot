import type { ReactNode } from "react";

type Props = { title: string; about: string; children: ReactNode };

/** A title, one line saying what the page shows, then the content. */
export function AdminPage({ title, about, children }: Props) {
  return (
    <section aria-label={title} className="flex min-h-0 min-w-0 flex-1 flex-col">
      <header className="flex min-h-[52px] shrink-0 flex-col justify-center border-b border-line px-4 py-2">
        <h1 className="text-title font-semibold tracking-[-0.01em]">{title}</h1>
        <p className="truncate text-meta text-ink-3">{about}</p>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 pt-3.5 pb-[22px] sm:px-6 sm:pt-[18px] sm:pb-7">
        <div className="flex max-w-[1100px] flex-col gap-4">{children}</div>
      </div>
    </section>
  );
}

export type Figure = { value: string; label: string };

/** Figures in one bordered strip of cells divided by lines, not in separate tiles. */
export function FigureStrip({ figures }: { figures: readonly Figure[] }) {
  return (
    <dl className="grid grid-cols-[repeat(auto-fit,minmax(130px,1fr))] gap-px overflow-hidden rounded-card border border-line bg-line">
      {figures.map((figure) => (
        <div key={figure.label} className="flex flex-col-reverse bg-surface px-3.5 py-[11px]">
          <dt className="text-meta text-ink-3">{figure.label}</dt>
          <dd className="text-figure font-semibold tracking-[-0.01em] tabular-nums">
            {figure.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
