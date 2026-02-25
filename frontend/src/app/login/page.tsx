"use client";

import { Suspense, useActionState } from "react";
import { useSearchParams } from "next/navigation";
import { login, LoginResult } from "@/lib/auth";
import { motion } from "framer-motion";
import Image from "next/image";
import { Lock, LogIn, Loader2 } from "lucide-react";

function LoginForm() {
  const searchParams = useSearchParams();
  const from = searchParams.get("from") || "/";

  const [state, formAction, isPending] = useActionState<
    LoginResult | null,
    FormData
  >(login, null);

  // Helper to extract file extension from URL
  const getLogoExtension = (url: string): string => {
    const urlPath = url.split("?")[0]; // Remove query parameters
    const ext = urlPath.split(".").pop() || "svg";
    return ext;
  };

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
            className="inline-flex items-center justify-center mb-6"
          >
            <Image
              src={
                process.env.NEXT_PUBLIC_LOGO_URL
                  ? `/custom-logo.${getLogoExtension(process.env.NEXT_PUBLIC_LOGO_URL)}`
                  : "/logo.svg"
              }
              alt="Logo"
              width={350}
              height={350}
              className="h-16 w-auto"
              priority
            />
          </motion.div>
          <h1 className="text-3xl font-bold text-foreground mb-2">
            Welcome Back
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
            {state?.error && (
              <motion.div
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                className="bg-destructive/10 border border-destructive/20 rounded-lg px-4 py-3 text-destructive text-sm"
              >
                {state.error}
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
