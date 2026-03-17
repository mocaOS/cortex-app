import { NextRequest, NextResponse } from "next/server";
import { decrypt } from "./lib/session";

// Routes that require authentication
const protectedRoutes = [
  "/",
  "/add",
  "/ask",
  "/collections",
  "/documents",
  "/explore",
  "/turbo",
  "/admin",
];

// Routes that are public (no auth required)
const publicRoutes = ["/login"];

export default async function proxy(req: NextRequest) {
  const path = req.nextUrl.pathname;
  
  // Check if current route is protected
  const isProtectedRoute = protectedRoutes.some(
    (route) => path === route || path.startsWith(route + "/")
  );
  
  // Check if current route is public
  const isPublicRoute = publicRoutes.some(
    (route) => path === route || path.startsWith(route + "/")
  );
  
  // Get session from cookie
  const cookie = req.cookies.get("session")?.value;
  const session = await decrypt(cookie);
  
  // Check if session is valid and not expired
  const isAuthenticated = session?.isAdmin && new Date() < new Date(session.expiresAt);
  
  // Redirect to login if accessing protected route without auth
  if (isProtectedRoute && !isAuthenticated) {
    const loginUrl = new URL("/login", req.nextUrl);
    loginUrl.searchParams.set("from", path);
    return NextResponse.redirect(loginUrl);
  }
  
  // Redirect to home if accessing login page while authenticated
  if (isPublicRoute && isAuthenticated) {
    return NextResponse.redirect(new URL("/", req.nextUrl));
  }
  
  return NextResponse.next();
}

// Configure which routes the proxy runs on
export const config = {
  matcher: [
    /*
     * Match all request paths except:
     * - api (API routes)
     * - _next/static (static files)
     * - _next/image (image optimization files)
     * - favicon.ico, sitemap.xml, robots.txt (metadata files)
     * - public folder files
     */
    "/((?!api|_next/static|_next/image|favicon.ico|sitemap.xml|robots.txt|.*\\.png$|.*\\.jpg$|.*\\.svg$).*)",
  ],
};
