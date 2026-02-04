"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { getApiKey } from "@/lib/auth";
import { setAdminApiKey, clearAdminApiKey } from "@/lib/api";

interface AuthContextType {
  isAuthReady: boolean;
}

const AuthContext = createContext<AuthContextType>({ isAuthReady: false });

/**
 * Hook to check if auth is initialized and ready.
 * Components should wait for isAuthReady before making authenticated API calls.
 */
export function useAuth() {
  return useContext(AuthContext);
}

/**
 * AuthProvider initializes the admin API key in localStorage when the user is authenticated.
 * This ensures all API requests include the proper authentication header.
 */
export default function AuthProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const [isAuthReady, setIsAuthReady] = useState(false);

  useEffect(() => {
    // Reset auth ready state on route change
    setIsAuthReady(false);

    // Clear API key on login page (after logout)
    if (pathname === "/login") {
      clearAdminApiKey();
      setIsAuthReady(true);
      return;
    }

    const initApiKey = async () => {
      try {
        const apiKey = await getApiKey();
        if (apiKey) {
          setAdminApiKey(apiKey);
        } else {
          // Clear any stale API key if not authenticated
          clearAdminApiKey();
        }
      } catch (error) {
        console.error("Failed to initialize API key:", error);
        clearAdminApiKey();
      } finally {
        setIsAuthReady(true);
      }
    };

    initApiKey();
  }, [pathname]);

  return (
    <AuthContext.Provider value={{ isAuthReady }}>
      {children}
    </AuthContext.Provider>
  );
}
