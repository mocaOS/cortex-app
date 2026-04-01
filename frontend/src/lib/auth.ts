"use server";

import { redirect } from "next/navigation";
import { createSession, deleteSession, verifySession } from "./session";

// Get admin credentials from environment
const ADMIN_EMAIL = process.env.ADMIN_EMAIL || "admin@example.com";
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "";
const ADMIN_API_KEY = process.env.ADMIN_API_KEY || "";

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

  // Check if admin password is configured
  if (!ADMIN_PASSWORD) {
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
