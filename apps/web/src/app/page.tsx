"use client";

import { api, API_URL, type User } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function LoginPage() {
  const router = useRouter();
  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: () => api<{ google: boolean; dev_login: boolean }>("/api/v1/auth/providers"),
    retry: false,
  });
  const [email, setEmail] = useState("ada@creativo.dev");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  async function enter(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      await api<User>("/api/v1/auth/dev-login", { json: { email, name: email.split("@")[0] } });
      router.push("/app");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed.");
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-5xl flex-col justify-center px-8 py-16">
      <p className="text-xs uppercase tracking-[0.28em] text-mute">Creativo</p>
      <h1 className="mt-6 max-w-xl font-serif text-6xl leading-[0.95] text-paper">
        The model is not the platform.
      </h1>
      <p className="mt-6 max-w-md text-lg text-mute">
        Submit an intent. The studio handles safety, credits, the queue, and the worker.
      </p>
      <div className="mt-10 flex max-w-sm flex-col gap-3">
        {providers.data?.google && (
          <a
            href={`${API_URL}/api/v1/auth/google`}
            className="border border-line px-4 py-3 text-center text-sm hover:border-paper"
          >
            Continue with Google
          </a>
        )}
        {providers.data?.dev_login !== false && (
          <form onSubmit={enter} className="flex flex-col gap-3">
            <input
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              type="email"
              required
              className="border border-line bg-transparent px-4 py-3 text-sm outline-none focus:border-accent"
              aria-label="Email"
            />
            <button
              type="submit"
              disabled={pending}
              className="bg-accent px-4 py-3 text-sm font-medium text-ink disabled:opacity-60"
            >
              {pending ? "Entering" : "Enter the studio"}
            </button>
          </form>
        )}
        {error && <p className="text-sm text-danger">{error}</p>}
        {providers.isError && (
          <p className="text-sm text-mute">The API is not reachable yet. Start it, then refresh.</p>
        )}
      </div>
      <p className="mt-8 text-sm text-mute">
        Development entrance grants 40 credits.{" "}
        <Link href="/app" className="text-paper underline decoration-line underline-offset-4">
          Studio
        </Link>
      </p>
    </main>
  );
}
