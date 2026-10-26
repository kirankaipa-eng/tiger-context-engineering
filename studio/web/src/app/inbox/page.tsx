"use client";

import clsx from "clsx";
import { Loader2, Sparkles } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { ProposalDetail } from "@/components/proposal";
import { Empty, Loading, when } from "@/components/ui";
import { api, streamNdjson, STATUS_LABEL, type ProposalSummary } from "@/lib/api";
import { useStudio } from "@/lib/studio";

const VIEWS = [
  { id: "mine", label: "Waiting for me" },
  { id: "open", label: "All open" },
  { id: "questions", label: "Questions" },
  { id: "done", label: "Done" },
];

function InboxView() {
  const { client, user, version, setScreen, bump, refreshCounts } = useStudio();
  const params = useSearchParams();
  const router = useRouter();
  const selected = params.get("id");
  const [view, setView] = useState("mine");
  const [items, setItems] = useState<ProposalSummary[] | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [scanning, setScanning] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!client) return;
    api.inbox(client, view).then((r) => { setItems(r.items); setCounts(r.counts); }).catch(() => setItems([]));
  }, [client, view]);
  useEffect(() => { load(); }, [load, user, version]);
  useEffect(() => { const t = setInterval(load, 5000); return () => clearInterval(t); }, [load]);
  useEffect(() => { if (!selected) setScreen({ screen: "inbox", title: "Inbox" }); }, [selected, setScreen]);
  useEffect(() => {
    if (!selected && items && items.length) router.replace(`/inbox?id=${items[0].id}`);
  }, [items, selected, router]);

  const scan = async () => {
    setScanning("Starting the curator");
    try {
      await streamNdjson("/api/curator/scan", { client }, (e) => {
        if (e.type === "progress") setScanning(e.data.message);
        if (e.type === "result") { setView("open"); }
      });
    } finally { setScanning(null); bump(); refreshCounts(); load(); }
  };

  return (
    <div className="flex h-full min-h-0">
      <section className="flex w-[340px] shrink-0 flex-col border-r border-line bg-sunk">
        <div className="flex flex-wrap gap-1.5 border-b border-line p-4">
          {VIEWS.map((v) => (
            <button key={v.id} onClick={() => setView(v.id)} className={clsx("rounded-full border px-2.5 py-0.5 text-xs", view === v.id ? "border-accent-line bg-accent-soft text-accent-ink" : "border-line bg-panel text-ink-2")}>
              {v.label} · {counts[v.id] ?? 0}
            </button>
          ))}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {items === null ? <div className="p-4"><Loading /></div> : items.length === 0 ? (
            <div className="p-4"><Empty>{view === "mine" ? "Nothing is waiting for you." : "Nothing here."}</Empty></div>
          ) : items.map((it) => (
            <button key={it.id} onClick={() => router.replace(`/inbox?id=${it.id}`)}
              className={clsx("block w-full border-b border-line-soft px-4 py-3.5 text-left", selected === it.id ? "border-l-[3px] border-l-accent bg-panel" : "hover:bg-panel")}>
              <div className={clsx("text-sm", selected === it.id && "font-semibold")}>{it.title}</div>
              <div className="mt-1 text-xs text-muted">
                {(it.areas ?? []).map((a) => it.area_titles?.[a] ?? a).join(", ") || "No area"} · {STATUS_LABEL[it.status] ?? it.status}
                {it.checks?.running && <Loader2 size={11} className="ml-1 inline animate-spin" />} · {when(it.updated_at)}
              </div>
            </button>
          ))}
        </div>
        <div className="border-t border-line p-3">
          <button onClick={scan} disabled={!!scanning} className="flex w-full items-center justify-center gap-2 rounded-md border border-[#bdb9ae] bg-panel px-3 py-2.5 text-sm hover:bg-ground disabled:opacity-60">
            {scanning ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} className="text-accent" />}
            {scanning ? "Curator is reading…" : "Run the curator on documents"}
          </button>
          {scanning && <div className="mt-1.5 truncate text-xs text-muted">{scanning}</div>}
        </div>
      </section>
      <section className="min-w-0 flex-1">
        {selected ? <ProposalDetail key={selected} id={selected} onChanged={load} /> : (
          <div className="p-8"><Empty>Select a proposal, or run the curator on this client&apos;s documents.</Empty></div>
        )}
      </section>
    </div>
  );
}

export default function InboxPage() {
  return <Suspense fallback={<div className="p-6"><Loading /></div>}><InboxView /></Suspense>;
}
