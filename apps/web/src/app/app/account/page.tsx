"use client";

import { api, type User } from "@/lib/api";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";

export default function AccountPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api<User>("/api/v1/auth/me"),
  });

  async function signOut() {
    await api("/api/v1/auth/logout", { method: "POST" });
    queryClient.clear();
    router.push("/");
  }

  return (
    <section className="max-w-lg px-8 py-6">
      <p className="text-xs uppercase tracking-[0.22em] text-mute">Account</p>
      <h1 className="mt-4 font-serif text-5xl">{me.data?.name || "—"}</h1>
      <p className="mt-3 text-mute">{me.data?.email}</p>
      <p className="mt-8 text-sm leading-6 text-mute">
        Google sign-in appears on the entrance page once `GOOGLE_CLIENT_ID` and
        `GOOGLE_CLIENT_SECRET` are set. This development account is keyed by email;
        a later Google login with the same email attaches to it, while Google’s
        permanent identity stays the provider subject.
      </p>
      <button type="button" onClick={signOut} className="mt-8 border border-line px-4 py-2 text-sm">
        Sign out
      </button>
    </section>
  );
}
