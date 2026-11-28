import { SkeletonLines } from "@/components/ui/skeleton";

export default function Loading() {
  return (
    <div className="flex-1 p-5">
      <SkeletonLines widths={["w-1/3", "w-2/3", "w-1/2"]} />
    </div>
  );
}
