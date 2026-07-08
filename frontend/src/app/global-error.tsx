"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";
import "./globals.css";

// Last-resort error boundary: only renders when the root layout itself throws,
// so it must provide its own <html>/<body>. Reports the crash to GlitchTip
// (regular route errors are captured by the SDK's automatic instrumentation;
// this file covers the one place that isn't).
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <html lang="en" className="dark">
      <body className="font-sans antialiased">
        <div className="min-h-screen bg-background text-foreground flex items-center justify-center p-6">
          <div className="max-w-md text-center space-y-4">
            <h1 className="text-2xl font-semibold">Something went wrong</h1>
            <p className="text-sm text-muted-foreground">
              An unexpected error occurred and has been reported.
              {error.digest && (
                <>
                  {" "}
                  Reference: <code className="font-mono">{error.digest}</code>
                </>
              )}
            </p>
            <button
              onClick={() => reset()}
              className="inline-flex items-center rounded-md border border-border bg-card px-4 py-2 text-sm hover:bg-muted transition-colors"
            >
              Try again
            </button>
          </div>
        </div>
      </body>
    </html>
  );
}
