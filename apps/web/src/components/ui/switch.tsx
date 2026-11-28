"use client";

import * as Primitive from "@radix-ui/react-switch";

type Props = {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  label: string;
};

/** A small track and thumb. The label is given by the caller's surrounding text or here. */
export function Switch({ checked, onCheckedChange, label }: Props) {
  return (
    <Primitive.Root
      checked={checked}
      onCheckedChange={onCheckedChange}
      aria-label={label}
      className="relative h-[15px] w-[26px] shrink-0 rounded-full bg-line-2 transition-colors duration-[120ms] data-[state=checked]:bg-accent"
    >
      <Primitive.Thumb className="absolute top-0.5 left-0.5 block size-[11px] rounded-full bg-surface transition-transform duration-[120ms] data-[state=checked]:translate-x-[11px]" />
    </Primitive.Root>
  );
}
