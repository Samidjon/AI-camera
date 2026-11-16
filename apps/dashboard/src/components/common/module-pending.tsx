import { Construction } from "lucide-react";

import { Badge } from "@/components/ui/badge";

/**
 * Honest placeholder for a module whose backend does not exist yet.
 * Shows what will be built and in which MVP — never mock data.
 */
export function ModulePending({
  mvp,
  title,
  description,
  planned,
}: {
  mvp: string;
  title: string;
  description: string;
  planned: string[];
}) {
  return (
    <div className="rounded-lg border border-dashed bg-card/40 p-6">
      <div className="flex flex-wrap items-center gap-2">
        <Construction className="size-4 text-status-warning" aria-hidden />
        <h2 className="text-sm font-semibold">{title}</h2>
        <Badge variant="outline" className="font-mono text-[10px]">
          Not implemented · {mvp}
        </Badge>
      </div>
      <p className="mt-2 max-w-2xl text-sm text-muted-foreground">{description}</p>
      {planned.length > 0 && (
        <ul className="mt-4 grid gap-x-6 gap-y-1.5 text-sm text-muted-foreground sm:grid-cols-2">
          {planned.map((item) => (
            <li key={item} className="flex items-center gap-2">
              <span className="size-1 rounded-full bg-muted-foreground/60" aria-hidden />
              {item}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
