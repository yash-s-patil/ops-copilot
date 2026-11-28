import type { ButtonHTMLAttributes } from "react";

type Variant = "default" | "primary" | "ghost";

const BASE =
  "inline-flex h-[30px] shrink-0 items-center gap-1.5 whitespace-nowrap rounded-control border font-medium " +
  "transition-colors duration-[120ms] pointer-coarse:min-h-11";

const VARIANTS: Record<Variant, string> = {
  default: "border-line-2 bg-surface hover:bg-raised",
  primary: "border-accent bg-accent text-on-accent hover:border-accent-ink hover:bg-accent-ink",
  ghost: "border-transparent text-ink-2 hover:bg-raised",
};

type Props = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; square?: boolean };

export function Button({ variant = "default", square = false, className = "", ...props }: Props) {
  const shape = square ? "w-[30px] justify-center pointer-coarse:min-w-11" : "px-[11px]";
  return (
    <button
      type="button"
      className={`${BASE} ${VARIANTS[variant]} ${shape} ${className}`}
      {...props}
    />
  );
}
