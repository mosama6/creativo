"use client";

import { api, type Credits, type User } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

const LINKS = [
  { href: "/app", label: "Create" },
  { href: "/app/history", label: "History" },
  { href: "/app/credits", label: "Credits" },
  { href: "/app/account", label: "Account" },
];

export function StudioFrame({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api<User>("/api/v1/auth/me"),
    retry: false,
  });
  const credits = useQuery({
    queryKey: ["credits"],
    queryFn: () => api<Credits>("/api/v1/credits"),
    enabled: Boolean(me.data),
  });

  useEffect(() => {
    if (me.isError) router.replace("/");
  }, [me.isError, router]);

  return (
    <div className="grid min-h-screen grid-cols-[220px_1fr]">
      <aside className="flex flex-col border-r border-line px-5 py-6">
        <Link href="/app" className="font-serif text-2xl">
          Creativo
        </Link>
        <nav className="mt-10 flex flex-col gap-1 text-sm">
          {LINKS.map((link) => {
            const active = pathname === link.href;
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`px-2 py-2 ${active ? "bg-panel text-paper" : "text-mute hover:text-paper"}`}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>
        <div className="mt-auto text-sm">
          <p className="text-mute">Available</p>
          <p className="mt-1 font-serif text-3xl">{credits.data?.available ?? "—"}</p>
          <p className="mt-3 text-mute">Reserved {credits.data?.reserved ?? "—"}</p>
        </div>
      </aside>
      <div className="min-w-0">{children}</div>
    </div>
  );
}
