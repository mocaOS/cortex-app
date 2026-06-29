"use server";

import { SignJWT, jwtVerify } from "jose";
import { cookies } from "next/headers";

// Session payload interface
export interface SessionPayload {
  isAdmin: boolean;
  email: string;
  expiresAt: Date;
}

// Get the secret key from environment, encode it for jose
const secretKey = process.env.SESSION_SECRET || "default-secret-key-min-32-characters-long";
const encodedKey = new TextEncoder().encode(secretKey);

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
    secure: process.env.NODE_ENV === "production",
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
