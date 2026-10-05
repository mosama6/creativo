"use client";

import { api, type Credits, type Transaction } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";

export default function CreditsPage() {
  const account = useQuery({
    queryKey: ["credits"],
    queryFn: () => api<Credits>("/api/v1/credits"),
  });
  const ledger = useQuery({
    queryKey: ["transactions"],
    queryFn: () => api<{ items: Transaction[] }>("/api/v1/credits/transactions"),
  });

  return (
    <section className="px-8 py-6">
      <p className="text-xs uppercase tracking-[0.22em] text-mute">Credits</p>
      <div className="mt-6 flex gap-12">
        <div>
          <p className="text-sm text-mute">Available</p>
          <p className="font-serif text-6xl">{account.data?.available ?? "—"}</p>
        </div>
        <div>
          <p className="text-sm text-mute">Reserved</p>
          <p className="font-serif text-6xl">{account.data?.reserved ?? "—"}</p>
        </div>
      </div>
      <table className="mt-10 w-full text-left text-sm">
        <thead className="text-mute">
          <tr>
            <th className="py-2 font-normal">Type</th>
            <th className="py-2 font-normal">Amount</th>
            <th className="py-2 font-normal">Available after</th>
            <th className="py-2 font-normal">Note</th>
          </tr>
        </thead>
        <tbody>
          {(ledger.data?.items ?? []).map((row) => (
            <tr key={row.id} className="border-t border-line">
              <td className="py-3">{row.type}</td>
              <td className="py-3">{row.amount}</td>
              <td className="py-3">{row.available_after}</td>
              <td className="py-3 text-mute">{row.description}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
