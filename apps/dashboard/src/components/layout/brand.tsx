import { ScanEye } from "lucide-react";

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <div className="grid size-7 place-items-center rounded-md bg-primary/15 text-primary ring-1 ring-primary/30">
        <ScanEye className="size-4" aria-hidden />
      </div>
      {!compact && (
        <div className="leading-tight">
          <div className="text-[13px] font-semibold tracking-tight">Camera Intelligence</div>
          <div className="font-mono text-[10px] tracking-[0.12em] text-muted-foreground uppercase">Control Center</div>
        </div>
      )}
    </div>
  );
}
