import Link from "next/link";

export default function NotFound() {
  return (
    <main className="grid min-h-dvh place-items-center bg-background p-6 text-center">
      <div>
        <p className="font-mono text-xs text-muted-foreground">404</p>
        <h1 className="mt-2 text-lg font-semibold">Page not found</h1>
        <Link href="/" className="mt-4 inline-block text-sm text-primary underline-offset-4 hover:underline">
          Back to Overview
        </Link>
      </div>
    </main>
  );
}
