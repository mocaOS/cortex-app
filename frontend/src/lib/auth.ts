"use server";

import { redirect } from "next/navigation";
import { createSession, deleteSession, verifySession } from "./session";

// Get admin credentials from environment. Deliberately NO fallback for
// ADMIN_EMAIL: substituting a default here would turn a missing/mis-loaded
// env file (wrong --env-file path, secrets not wired into the deployment)
// into "Invalid email or password" against credentials the user knows are
// right — a cold diagnostic trail. Missing credentials must fail loudly as
// "not configured", the same way ADMIN_PASSWORD already does. The compose
// files default ADMIN_EMAIL to admin@example.com at the env layer, where
// the substitution is visible.
const ADMIN_EMAIL = process.env.ADMIN_EMAIL || "";
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "";
const ADMIN_API_KEY = process.env.ADMIN_API_KEY || "";

if (!ADMIN_EMAIL || !ADMIN_PASSWORD) {
  const missing = [
    !ADMIN_EMAIL && "ADMIN_EMAIL",
    !ADMIN_PASSWORD && "ADMIN_PASSWORD",
  ]
    .filter(Boolean)
    .join(", ");
  console.warn(
    `[cortex] ${missing} not set — admin login is disabled and will answer ` +
      `"Admin authentication not configured". Check that your deployment ` +
      `actually loads your .env into the frontend container ` +
      `(docker exec <frontend> printenv ADMIN_EMAIL).`
  );
}

export interface LoginResult {
  success: boolean;
  error?: string;
  apiKey?: string;
}

/**
 * Authenticate admin with email and password.
 * Validates against environment variables.
 */
export async function login(
  _prevState: LoginResult | null,
  formData: FormData
): Promise<LoginResult> {
  const email = formData.get("email") as string;
  const password = formData.get("password") as string;

  // Validate inputs
  if (!email || !password) {
    return {
      success: false,
      error: "Email and password are required",
    };
  }

  // Check if admin credentials are configured — a missing ADMIN_EMAIL must
  // NOT degrade into "Invalid email or password" (see note at top of file).
  if (!ADMIN_EMAIL || !ADMIN_PASSWORD) {
    return {
      success: false,
      error: "Admin authentication not configured",
    };
  }

  // Validate credentials
  if (email !== ADMIN_EMAIL || password !== ADMIN_PASSWORD) {
    return {
      success: false,
      error: "Invalid email or password",
    };
  }

  // Create session
  await createSession(email);

  // Return success with API key — client sets localStorage before redirect
  // to avoid race condition where AuthProvider hasn't initialized yet
  return { success: true, apiKey: ADMIN_API_KEY || undefined };
}

/**
 * Log out the current user.
 */
export async function logout(): Promise<void> {
  await deleteSession();
  redirect("/login");
}

/**
 * Check if user is authenticated.
 */
export async function isAuthenticated(): Promise<boolean> {
  const session = await verifySession();
  return session !== null && session.isAdmin;
}

/**
 * Get the current user's email if authenticated.
 */
export async function getCurrentUser(): Promise<string | null> {
  const session = await verifySession();
  return session?.email || null;
}

/**
 * Get the admin API key if authenticated.
 * This is used by the client to make authenticated API requests.
 */
export async function getApiKey(): Promise<string | null> {
  const session = await verifySession();
  if (!session?.isAdmin) {
    return null;
  }
  return ADMIN_API_KEY || null;
}
