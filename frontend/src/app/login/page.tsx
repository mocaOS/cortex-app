"use client";

import { Suspense, useActionState, useEffect, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { login, LoginResult } from "@/lib/auth";
import { setAdminApiKey } from "@/lib/api";
import { motion } from "framer-motion";
import Image from "next/image";
import { Lock, LogIn, Loader2 } from "lucide-react";

// Only allow same-origin, absolute-path redirects. Anything else (absolute URL,
// protocol-relative "//evil.com", or a non-"/" value) falls back to "/" so the
// post-login redirect can't be used for open-redirect phishing.
function safeRedirectTarget(raw: string | null): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) {
    return "/";
  }
  return raw;
}

function LoginForm() {
  const searchParams = useSearchParams();
  const from = safeRedirectTarget(searchParams.get("from"));
  const router = useRouter();

  const [state, formAction, isPending] = useActionState<
    LoginResult | null,
    FormData
  >(login, null);

  const [configError, setConfigError] = useState<string | null>(null);

  // Set API key and redirect on successful login
  useEffect(() => {
    if (state?.success) {
      if (state.apiKey) {
        setAdminApiKey(state.apiKey);
        router.push(from);
      } else {
        // Auth succeeded but the server returned no API key — ADMIN_API_KEY is
        // unset on the backend. Redirecting would land in an app where every
        // request throws "Not authenticated". Surface it instead.
        setConfigError(
          "Signed in, but the server has no ADMIN_API_KEY configured, so the app can't make API calls. Set ADMIN_API_KEY on the backend and try again."
        );
      }
    }
  }, [state?.success, state?.apiKey, router, from]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-6">
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="w-full max-w-md"
      >
        {/* Logo and Title */}
        <div className="text-center mb-8">
          <motion.div
            initial={{ scale: 0.5, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ delay: 0.2, duration: 0.5 }}
            className="inline-flex items-center justify-center mb-12"
          >
            <Image
              src="/brand/cortex_logo_white.svg"
              alt="Cortex"
              width={96}
              height={96}
              className="h-24 w-24"
              priority
              unoptimized
            />
          </motion.div>
          <h1 className="text-3xl font-bold text-foreground mb-2">
            Welcome to Cortex
          </h1>
          <p className="text-muted-foreground">
            Sign in to access the knowledge base
          </p>
        </div>

        {/* Login Form */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.3, duration: 0.5 }}
          className="glass rounded-2xl p-8"
        >
          <form action={formAction} className="space-y-6">
            {/* Error Message */}
            {(state?.error || configError) && (
              <motion.div
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                className="bg-destructive/10 border border-destructive/20 rounded-lg px-4 py-3 text-destructive text-sm"
              >
                {state?.error || configError}
              </motion.div>
            )}

            {/* Email Field */}
            <div>
              <label
                htmlFor="email"
                className="block text-sm font-medium text-foreground mb-2"
              >
                Email
              </label>
              <input
                type="email"
                id="email"
                name="email"
                required
                autoComplete="email"
                className="w-full px-4 py-3 bg-background border border-border rounded-lg text-foreground placeholder-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:border-ring transition-colors"
                placeholder="admin@example.com"
              />
            </div>

            {/* Password Field */}
            <div>
              <label
                htmlFor="password"
                className="block text-sm font-medium text-foreground mb-2"
              >
                Password
              </label>
              <input
                type="password"
                id="password"
                name="password"
                required
                autoComplete="current-password"
                className="w-full px-4 py-3 bg-background border border-border rounded-lg text-foreground placeholder-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:border-ring transition-colors"
                placeholder="Enter your password"
              />
            </div>

            {/* Hidden redirect field */}
            <input type="hidden" name="redirectTo" value={from} />

            {/* Submit Button */}
            <button
              type="submit"
              disabled={isPending}
              className="w-full py-3 px-4 bg-accent hover:bg-accent/90 disabled:opacity-50 disabled:cursor-not-allowed text-accent-foreground font-medium rounded-lg transition-all duration-200 flex items-center justify-center gap-2"
            >
              {isPending ? (
                <>
                  <Loader2 className="h-5 w-5 animate-spin" />
                  <span>Signing in...</span>
                </>
              ) : (
                <>
                  <LogIn className="w-5 h-5" />
                  <span>Sign In</span>
                </>
              )}
            </button>
          </form>
        </motion.div>

        {/* Footer */}
        <div className="flex items-center justify-center gap-2 mt-6 text-muted-foreground text-sm">
          <Lock className="w-4 h-4" />
          <span>Admin Access Only</span>
        </div>
      </motion.div>
    </div>
  );
}

function LoginFallback() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-6">
      <div className="w-full max-w-md text-center">
        <Loader2 className="h-8 w-8 animate-spin mx-auto text-muted-foreground" />
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<LoginFallback />}>
      <LoginForm />
    </Suspense>
  );
}
