"use server";

import { SignJWT, jwtVerify } from "jose";
import { cookies } from "next/headers";

// Session payload interface
export interface SessionPayload {
  isAdmin: boolean;
  email: string;
  expiresAt: Date;
}

// Get the secret key from environment, encode it for jose.
//
// SESSION_SECRET signs the admin session JWT — if it is guessable, anyone can
// forge an admin session. In production we refuse to start on a missing, too
// short, or known-placeholder secret (fail-safe). Development keeps a
// convenient fallback but warns. A correctly-configured deployment (a strong
// 32+ char secret) is unaffected.
const DEV_FALLBACK_SECRET = "default-secret-key-min-32-characters-long";
const KNOWN_WEAK_SECRETS = new Set([
  DEV_FALLBACK_SECRET,
  "secret",
  "your-session-secret-key-at-least-32-characters-long",
]);

function resolveSessionSecret(): string {
  const configured = process.env.SESSION_SECRET;
  const insecure =
    !configured ||
    configured.length < 32 ||
    KNOWN_WEAK_SECRETS.has(configured) ||
    configured.startsWith("CHANGE_ME");

  if (insecure) {
    if (process.env.NODE_ENV === "production") {
      throw new Error(
        "SESSION_SECRET is missing, shorter than 32 characters, or a known " +
          "placeholder. Set a strong SESSION_SECRET (e.g. `openssl rand -hex 32`) " +
          "before starting Cortex in production."
      );
    }
    console.warn(
      "[cortex] SESSION_SECRET is unset or weak — using an insecure development " +
        "fallback. Set a strong SESSION_SECRET before deploying to production."
    );
    return configured || DEV_FALLBACK_SECRET;
  }
  return configured;
}

const secretKey = resolveSessionSecret();
const encodedKey = new TextEncoder().encode(secretKey);

// The session cookie's Secure flag. Browsers silently drop Secure cookies on
// plain-HTTP connections, so a self-hosted instance served over HTTP on a LAN
// (no TLS termination in front) would accept the login but never store the
// cookie — SESSION_COOKIE_SECURE=false is the runtime escape hatch for that
// setup. NODE_ENV alone can't express this: Next.js inlines it at build time,
// so the flag would be baked into the image. SESSION_COOKIE_SECURE is an
// ordinary env var, read from process.env at runtime on every login.
function resolveCookieSecure(): boolean {
  const configured = process.env.SESSION_COOKIE_SECURE?.trim().toLowerCase();
  if (configured === "true" || configured === "1") return true;
  if (configured === "false" || configured === "0") return false;
  return process.env.NODE_ENV === "production";
}

/**
 * Encrypt session payload into a JWT token.
 */
export async function encrypt(payload: SessionPayload): Promise<string> {
  return new SignJWT({ ...payload, expiresAt: payload.expiresAt.toISOString() })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt()
    .setExpirationTime("24h")
    .sign(encodedKey);
}

/**
 * Decrypt and verify a JWT token.
 */
export async function decrypt(session: string | undefined): Promise<SessionPayload | null> {
  if (!session) {
    return null;
  }
  
  try {
    const { payload } = await jwtVerify(session, encodedKey, {
      algorithms: ["HS256"],
    });
    
    return {
      isAdmin: payload.isAdmin as boolean,
      email: payload.email as string,
      expiresAt: new Date(payload.expiresAt as string),
    };
  } catch (error) {
    // Expected on every unauthenticated request (no/expired cookie) — only log
    // in development to avoid flooding production logs.
    if (process.env.NODE_ENV !== "production") {
      console.log("Failed to verify session:", error);
    }
    return null;
  }
}

/**
 * Create a new session for an authenticated admin.
 */
export async function createSession(email: string): Promise<void> {
  const expiresAt = new Date(Date.now() + 24 * 60 * 60 * 1000); // 24 hours
  
  const session = await encrypt({
    isAdmin: true,
    email,
    expiresAt,
  });
  
  const cookieStore = await cookies();

  cookieStore.set("session", session, {
    httpOnly: true,
    secure: resolveCookieSecure(),
    expires: expiresAt,
    sameSite: "lax",
    path: "/",
  });
}

/**
 * Get the current session from cookies.
 */
export async function getSession(): Promise<SessionPayload | null> {
  const cookieStore = await cookies();
  const session = cookieStore.get("session")?.value;
  return decrypt(session);
}

/**
 * Delete the session (logout).
 */
export async function deleteSession(): Promise<void> {
  const cookieStore = await cookies();
  cookieStore.delete("session");
}

/**
 * Verify the session is valid and not expired.
 */
export async function verifySession(): Promise<SessionPayload | null> {
  const session = await getSession();
  
  if (!session) {
    return null;
  }
  
  // Check if session is expired
  if (new Date() > session.expiresAt) {
    await deleteSession();
    return null;
  }
  
  return session;
}
