/** Lines shaped like the content they stand in for, so nothing moves when it arrives. */
export function SkeletonLines({ widths }: { widths: readonly string[] }) {
  return (
    <div aria-hidden="true" className="flex flex-col gap-2">
      {widths.map((width, index) => (
        <span key={index} className={`block h-3 rounded-xs bg-raised ${width}`} />
      ))}
    </div>
  );
}
