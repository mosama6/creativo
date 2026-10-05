"use client";

import { api, API_URL, type Generation } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

export default function HistoryPage() {
  const history = useQuery({
    queryKey: ["history"],
    queryFn: () => api<{ items: Generation[] }>("/api/v1/generations"),
  });

  return (
    <section className="px-8 py-6">
      <p className="text-xs uppercase tracking-[0.22em] text-mute">History</p>
      <div className="mt-6 grid grid-cols-2 gap-4 xl:grid-cols-3">
        {(history.data?.items ?? []).map((item) => (
          <Link key={item.id} href={`/app?g=${item.id}`} className="border border-line bg-panel p-3">
            {item.status === "completed" ? (
              <HistoryImage id={item.id} alt={item.prompt} />
            ) : (
              <div className="flex h-40 items-center justify-center text-sm text-mute">{item.status}</div>
            )}
            <p className="mt-3 line-clamp-2 text-sm">{item.prompt}</p>
            <p className="mt-2 text-xs text-mute">
              {item.model} · {item.credit_price} credit{item.credit_price === 1 ? "" : "s"}
            </p>
          </Link>
        ))}
      </div>
      {history.data?.items.length === 0 && <p className="mt-8 text-mute">No generations yet.</p>}
    </section>
  );
}

function HistoryImage({ id, alt }: { id: string; alt: string }) {
  const image = useQuery({
    queryKey: ["output", id],
    queryFn: async () => {
      const response = await fetch(`${API_URL}/api/v1/generations/${id}/output`, {
        credentials: "include",
        headers: { "X-Creativo-Client": "web" },
      });
      if (!response.ok) throw new Error("missing");
      return URL.createObjectURL(await response.blob());
    },
  });
  if (!image.data) return <div className="h-40 bg-ink" />;
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={image.data} alt={alt} className="h-40 w-full object-cover" />;
}
