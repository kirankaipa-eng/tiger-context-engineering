"use client";

import clsx from "clsx";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { Button, ErrorNote, Loading } from "@/components/ui";
import { api } from "@/lib/api";
import { useStudio } from "@/lib/studio";

type Item = Awaited<ReturnType<typeof api.history>>["items"][number];

export default function HistoryPage() {
  const { client, version, setScreen, user, areas, bump, refreshCounts } = useStudio();
  const router = useRouter();
  const [items, setItems] = useState<Item[] | null>(null);
  const [area, setArea] = useState("all");
  const [undoing, setUndoing] = useState<Item | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (client) api.history(client).then((r) => setItems(r.items)).catch((e) => setError(e.message)); }, [client, version]);
  useEffect(() => { setScreen({ screen: "history", title: "History" }); }, [setScreen]);
  const shown = useMemo(() => (items ?? []).filter((h) => area === "all" || h.areas.some((a) => a.id === area)), [items, area]);
  const usedAreas = useMemo(() => [...new Set((items ?? []).flatMap((h) => h.areas.map((a) => a.id)))], [items]);

  const undo = async () => {
    if (!undoing) return;
    setBusy(true); setError(null);
    try { const p = await api.undo(undoing.sha); bump(); refreshCounts(); router.push(`/inbox?id=${p.id}`); }
    catch (e: any) { setError(e.message); } finally { setBusy(false); } // eslint-disable-line @typescript-eslint/no-explicit-any
  };

  return (
    <div className="mx-auto max-w-5xl space-y-4 px-8 py-6">
      <div className="flex items-center gap-3"><h1 className="text-2xl font-semibold">History</h1><span className="text-sm text-muted">Every approved change, who approved it, and why</span></div>
      <div className="flex flex-wrap gap-1.5">
        {["all", ...usedAreas].map((a) => (
          <button key={a} onClick={() => setArea(a)} className={clsx("rounded-full border px-2.5 py-0.5 text-xs", area === a ? "border-accent-line bg-accent-soft text-accent-ink" : "border-line bg-panel")}>
            {a === "all" ? "All areas" : areas.find((x) => x.id === a)?.title ?? a}
          </button>
        ))}
      </div>
      {undoing && (
        <div className="flex items-center gap-4 rounded-lg border border-accent-line bg-accent-soft/40 p-4">
          <div className="flex-1"><b className="text-sm">Undo “{undoing.subject}”?</b>
            <div className="text-[13px] text-ink-2">This opens a proposal to reverse it. Its owners approve it like any other change; nothing changes until they do.</div></div>
          <Button primary onClick={undo} disabled={busy}>Propose undo</Button><Button onClick={() => setUndoing(null)}>Cancel</Button>
        </div>
      )}
      <ErrorNote error={error} />
      {items === null ? <Loading /> : (
        <div className="overflow-hidden rounded-lg border border-line bg-panel">
          {shown.map((h, i) => (
            <div key={h.sha} className={clsx("grid grid-cols-[90px_14px_1fr_auto] items-start gap-3.5 px-4 py-3.5", i > 0 && "border-t border-line-soft", i === 0 && "bg-[#f3f6fc]")}>
              <span className="pt-0.5 text-[13px] text-muted">{new Date(h.at).toLocaleDateString(undefined, { day: "numeric", month: "short" })}</span>
              <span className={clsx("mt-1.5 h-2.5 w-2.5 rounded-full", h.is_revert ? "bg-warn-line" : h.initial ? "bg-line" : "bg-accent")} />
              <div>
                <div className="text-sm font-semibold">{h.subject}</div>
                <div className="text-[13px] text-muted">
                  {h.areas.map((a) => a.title).join(", ") || "Setup"}
                  {h.approvals.length > 0 && <> · approved by {h.approvals.map((a) => a.name).join(", ")}</>}
                  {h.evidence[0] && <> · source: {h.evidence[0].title ?? h.evidence[0].doc}, p.{h.evidence[0].page}</>}
                </div>
              </div>
              <div className="flex gap-2">
                {h.proposal && <Link href={`/inbox?id=${h.proposal}`} className="rounded-md border border-[#bdb9ae] px-3 py-1.5 text-[13px] hover:bg-sunk">View</Link>}
                {!h.initial && <button disabled={!user} onClick={() => setUndoing(h)} className="rounded-md border border-[#bdb9ae] px-3 py-1.5 text-[13px] hover:bg-sunk disabled:opacity-40">Undo</button>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
