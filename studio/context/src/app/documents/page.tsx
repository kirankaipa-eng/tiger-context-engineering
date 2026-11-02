"use client";

import clsx from "clsx";
import { Loader2, Sparkles } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { ScanCanvas } from "@/components/canvases";
import { Button, Empty, Loading, Pill } from "@/components/ui";
import { api, streamNdjson, type ScanReport } from "@/lib/api";
import { useStudio } from "@/lib/studio";

type Docs = Awaited<ReturnType<typeof api.documents>>;
type Doc = Awaited<ReturnType<typeof api.document>>;

function DocumentsView() {
  const { client, version, setScreen, user, bump, refreshCounts } = useStudio();
  const params = useSearchParams();
  const [docs, setDocs] = useState<Docs | null>(null);
  const [open, setOpen] = useState<string | null>(params.get("doc"));
  const [doc, setDoc] = useState<Doc | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [scanning, setScanning] = useState(false);
  const [report, setReport] = useState<ScanReport | null>(null);
  const pageRef = useRef<HTMLDivElement | null>(null);
  const focusPage = Number(params.get("page") ?? 0);

  useEffect(() => { if (client) api.documents(client).then((d) => { setDocs(d); setReport(d.last_scan); }).catch(() => setDocs({ documents: [], last_scan: null })); }, [client, version]);
  useEffect(() => { if (client && open) api.document(client, open).then(setDoc).catch(() => setDoc(null)); }, [client, open]);
  useEffect(() => { setScreen({ screen: "documents", title: open ? doc?.title ?? open : "Documents", document: open }); }, [open, doc, setScreen]);
  useEffect(() => { pageRef.current?.scrollIntoView({ block: "start" }); }, [doc]);

  const scan = async () => {
    setScanning(true); setLog([]);
    try {
      await streamNdjson("/api/curator/scan", { client }, (e) => {
        if (e.type === "progress") setLog((l) => [...l, e.data.message]);
        if (e.type === "result") setReport(e.data);
        if (e.type === "error") setLog((l) => [...l, `Error: ${e.data.message}`]);
      });
    } finally { setScanning(false); bump(); refreshCounts(); }
  };

  return (
    <div className="flex h-full min-h-0">
      <section className="flex w-[340px] shrink-0 flex-col border-r border-line bg-sunk">
        <div className="border-b border-line p-4">
          <div className="text-base font-semibold">Documents</div>
          <div className="text-xs text-muted">What the curator reads for this client. Documents stay where they are; only cited lines are kept with proposals.</div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {docs === null ? <div className="p-4"><Loading /></div> : docs.documents.length === 0 ? <div className="p-4"><Empty>No documents for this client yet.</Empty></div> :
            docs.documents.map((d) => (
              <button key={d.doc} onClick={() => setOpen(d.doc)} className={clsx("block w-full border-b border-line-soft px-4 py-3 text-left", open === d.doc ? "border-l-[3px] border-l-accent bg-panel" : "hover:bg-panel")}>
                <div className="text-sm">{d.title}</div><div className="mt-0.5 text-xs text-muted">{d.kind} · {d.date} · {d.pages} page{d.pages === 1 ? "" : "s"}</div>
              </button>
            ))}
        </div>
        <div className="border-t border-line p-3">
          <Button primary onClick={scan} disabled={scanning || !user} className="flex w-full items-center justify-center gap-2">{scanning ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}{scanning ? "Reading…" : "Run the curator"}</Button>
        </div>
      </section>
      <section className="min-w-0 flex-1 overflow-y-auto px-8 py-6">
        {(scanning || log.length > 0) && (
          <div className="mb-4 rounded-lg border border-line bg-panel p-4 text-[13px]">
            <div className="lbl mb-2">Curator</div>
            {log.map((l, i) => <div key={i} className="text-ink-2">{l}</div>)}
            {scanning && <div className="pulse-soft text-muted">working…</div>}
          </div>
        )}
        {report && !open && <ScanCanvas report={report} />}
        {open && !doc && <Loading />}
        {doc && (
          <article className="mx-auto max-w-3xl space-y-5">
            <div><h1 className="text-2xl font-semibold">{doc.title}</h1><div className="mt-1 flex gap-2 text-[13px] text-muted"><Pill>{doc.kind}</Pill>{doc.date}</div></div>
            {doc.pages.map((p) => (
              <div key={p.page} ref={p.page === focusPage && open === params.get("doc") ? pageRef : undefined} className={clsx("rounded-lg border bg-panel p-5", p.page === focusPage && open === params.get("doc") ? "border-accent-line ring-2 ring-accent-soft" : "border-line")}>
                <div className="lbl mb-2">Page {p.page}</div>
                <div className="space-y-3 text-sm leading-relaxed">{p.paragraphs.map((t, i) => <p key={i}>{t}</p>)}</div>
              </div>
            ))}
          </article>
        )}
        {!open && !report && !scanning && <Empty>Open a document, or run the curator to read them all.</Empty>}
      </section>
    </div>
  );
}

export default function DocumentsPage() {
  return <Suspense fallback={<div className="p-6"><Loading /></div>}><DocumentsView /></Suspense>;
}
