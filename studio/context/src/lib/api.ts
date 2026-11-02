// Context Studio API client. Every call says who is asking (X-Studio-User) and for which client.

export const API_BASE = process.env.NEXT_PUBLIC_STUDIO_API ?? "http://127.0.0.1:8700";

export type Person = { id: string; name: string; role: string; areas: string[] };
export type Area = { id: string; title: string; owners: string[]; paths: string[] };
export type ClientInfo = { name: string; title: string; line: string; units: string[]; has_data: boolean };

export type Evidence = { quote: string; doc: string; title?: string; page?: number };
export type CheckItem = { id: string; label: string; ok: boolean; detail?: string };
export type Finding = { unit: string; entity: string; slot: string; check: string; violations: number; rows: number; detail: string; sample: unknown[] };
export type ImpactRow = { metric: string; label: string; group: string[]; format: string | null; before: number | null; after: number | null; before_fmt: string; after_fmt: string; changed: boolean };
export type Comment = { by: string; name?: string; kind: string; text: string; at: string };
export type Approval = { by: string; name: string; areas: string[]; at: string };

export type Proposal = {
  id: string; title: string; summary: string; kind: string; client: string; status: string;
  areas: string[]; area_titles: Record<string, string>; reviewers: Record<string, string[]>;
  author: { id: string; name: string; kind: string }; confidence?: string | null; layer?: string;
  evidence: Evidence[]; data_proof?: { agrees?: boolean | null; summary?: string } | null;
  set_aside?: { doc: string; page?: number; why: string }[];
  checks: { running?: boolean; passed?: boolean; items?: CheckItem[]; findings_cleared?: Finding[]; findings_new?: Finding[] };
  impact: { metrics?: string[]; by?: string[]; as_of?: string; rows?: ImpactRow[]; changed?: number };
  diff?: { files: { status: string; path: string }[]; patch: string; behind_main: number };
  comments: Comment[]; approvals: Approval[]; created_at: string; updated_at: string;
  merged_sha?: string; about?: Record<string, unknown>; ops?: Record<string, unknown>[];
  waiting_areas?: string[]; can_approve?: boolean; can_review?: boolean;
};
export type ProposalSummary = Pick<Proposal, "id" | "title" | "summary" | "kind" | "client" | "status" | "areas" | "area_titles" | "reviewers" | "author" | "confidence" | "created_at" | "updated_at" | "approvals" | "layer"> & {
  checks: { running?: boolean; passed?: boolean }; evidence_count: number; changed?: number;
};

export type Attribute = { name: string; label: string; range: string; kind: string; required: boolean; multivalued: boolean; description: string; layer: string; sensitivity?: string | null; agent_visible: boolean; change?: string; values?: string[] };
export type ClassInfo = {
  name: string; label: string; curie: string; layer: string; group: string; abstract: boolean; is_a: string[];
  parent?: string; description: string; aliases: string[]; identity: string; attributes: Attribute[];
  sources: { unit: string; unit_name: string; source_system: string; tables: string[]; transform: string[]; notes: string[]; rows: number | null; slots_mapped: number; slots_total: number; unit_view: string }[];
  metrics: string[]; customized: string[]; view: string;
};
export type Relationship = { from: string; slot: string; to: string; label: string; layer: string; multivalued: boolean };
export type EnumInfo = { name: string; layer: string; description: string; values: { value: string; description: string }[] };
export type MetricDef = { name: string; label: string; description: string; format: string; source: string; entity?: string; measure?: string; filter?: string; ratio?: string[]; sum_of?: string[]; as_of?: string; dimensions?: string[] };
export type OntologyModel = {
  client: { name: string; title: string; line: string; units: { code: string; name: string; source_system: string }[] };
  classes: ClassInfo[]; relationships: Relationship[]; enums: EnumInfo[]; metrics: MetricDef[];
  pending: Record<string, { id: string; title: string; status: string }[]>;
};

let currentUser = "";
export function setApiUser(id: string) { currentUser = id; }

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(currentUser ? { "X-Studio-User": currentUser } : {}), ...(init.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not json */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

const q = (params: Record<string, string | undefined>) =>
  "?" + Object.entries(params).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => `${k}=${encodeURIComponent(v!)}`).join("&");

export const api = {
  people: () => call<{ people: Person[]; areas: Area[] }>("/api/people"),
  clients: () => call<{ clients: ClientInfo[]; default: string }>("/api/clients"),
  inbox: (client: string, view: string) => call<{ counts: Record<string, number>; items: ProposalSummary[] }>(`/api/inbox${q({ client, view })}`),
  proposal: (id: string) => call<Proposal>(`/api/proposals/${id}`),
  createProposal: (body: { client: string; title: string; summary?: string; ops: Record<string, unknown>[]; evidence?: Evidence[] }) =>
    call<Proposal>("/api/proposals", { method: "POST", body: JSON.stringify(body) }),
  approve: (id: string) => call<Proposal>(`/api/proposals/${id}/approve`, { method: "POST" }),
  requestChanges: (id: string, text: string) => call<Proposal>(`/api/proposals/${id}/request-changes`, { method: "POST", body: JSON.stringify({ text }) }),
  comment: (id: string, text: string) => call<Proposal>(`/api/proposals/${id}/comments`, { method: "POST", body: JSON.stringify({ text }) }),
  answer: (id: string, text: string) => call<Proposal>(`/api/proposals/${id}/answer`, { method: "POST", body: JSON.stringify({ text }) }),
  close: (id: string, reason: string) => call<Proposal>(`/api/proposals/${id}/close`, { method: "POST", body: JSON.stringify({ reason }) }),
  recheck: (id: string) => call<Proposal>(`/api/proposals/${id}/recheck`, { method: "POST" }),
  map: (client: string) => call<OntologyModel>(`/api/map${q({ client })}`),
  concepts: (client: string) => call<{ concepts: (Pick<ClassInfo, "name" | "label" | "group" | "layer" | "description" | "abstract" | "customized" | "metrics"> & { pending: number })[] }>(`/api/concepts${q({ client })}`),
  concept: (client: string, name: string) => call<ClassInfo & {
    relationships: Relationship[]; definition: { text: string; updated: string; verified: unknown } | null;
    pending: { id: string; title: string; status: string }[]; owners: string[]; definition_owners: string[];
  }>(`/api/concepts/${name}${q({ client })}`),
  metrics: (client: string) => call<{ metrics: MetricDef[]; dimensions: { name: string; label: string; entities: string[] }[] }>(`/api/metrics${q({ client })}`),
  metric: (client: string, name: string, by?: string) => call<{
    definition: MetricDef; dimensions: string[]; by: string[]; values: { columns: string[]; rows: unknown[][] } | null;
    parts: MetricDef[]; as_of: string; pending: { id: string; title: string; status: string }[];
  }>(`/api/metrics/${name}${q({ client, by })}`),
  preview: (body: { client: string; ops: Record<string, unknown>[]; metrics: string[]; by: string[] }) =>
    call<{ checks: CheckItem[]; impact: Proposal["impact"] }>("/api/preview", { method: "POST", body: JSON.stringify(body) }),
  mappings: (client: string) => call<{ units: { code: string; name: string; source_system: string; entities: { entity: string; table?: string; slots: number }[] }[] }>(`/api/mappings${q({ client })}`),
  mapping: (client: string, unit: string, entity: string) => call<{
    unit: { code: string; name: string; source_system: string }; entity: string; label: string; table?: string; query?: string; note?: string;
    source_schema?: string; view: string; slots: { slot: string; column?: string; expr?: string; note?: string; sensitive: boolean }[];
    preview: { columns: string[]; rows: unknown[][] }; findings: Finding[]; pending: { id: string; title: string; status: string }[];
  }>(`/api/mappings/${unit}/${entity}${q({ client })}`),
  history: (client: string) => call<{ items: {
    sha: string; subject: string; author: string; at: string; proposal: string | null; client: string | null;
    approvals: Approval[]; evidence: Evidence[]; areas: { id: string; title: string }[]; files: string[]; is_revert: boolean; initial: boolean;
  }[] }>(`/api/history${q({ client })}`),
  undo: (sha: string) => call<Proposal>(`/api/history/${sha}/undo`, { method: "POST" }),
  documents: (client: string) => call<{ documents: { doc: string; title: string; kind: string; date: string; source: string; pages: number }[]; last_scan: ScanReport | null }>(`/api/documents${q({ client })}`),
  chatSessions: (client: string) => call<{ sessions: { id: string; title: string; turns: number; updated_at: string }[] }>(`/api/chat/sessions${q({ client })}`),
  chatSession: (client: string, id: string) => call<{ id: string; title: string; turns: { user: string; answer: string; steps: string[]; canvases: unknown[]; at: string }[] }>(`/api/chat/sessions/${id}${q({ client })}`),
  deleteChatSession: (client: string, id: string) => call<{ deleted: string }>(`/api/chat/sessions/${id}${q({ client })}`, { method: "DELETE" }),
  document: (client: string, doc: string) => call<{ doc: string; title: string; kind: string; date: string; pages: { page: number; paragraphs: string[] }[] }>(`/api/documents/${doc}${q({ client })}`),
};

export type ScanResult = { action: string; id?: string; title?: string; why?: string; doc?: string; page?: number };
export type ScanReport = {
  client: string; documents: number; claims: number; results: ScanResult[];
  set_aside: { doc: string; title: string; page: number; quote: string; why: string }[];
  llm: boolean; evaluation?: { needles: number; found: number; decoys: number; decoys_acted_on: number; set_aside: number };
};

// NDJSON streaming (chat and curator scans)
export async function streamNdjson(path: string, body: unknown, onEvent: (e: { type: string; data: any }) => void, signal?: AbortSignal) { // eslint-disable-line @typescript-eslint/no-explicit-any
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST", signal,
    headers: { "content-type": "application/json", ...(currentUser ? { "X-Studio-User": currentUser } : {}) },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* stream */ }
    throw new Error(String(detail));
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line) continue;
      try { onEvent(JSON.parse(line)); } catch { /* partial or non-json line */ }
    }
  }
}

export const STATUS_LABEL: Record<string, string> = {
  checking: "Checks running", in_review: "Waiting for review", changes_requested: "Changes requested",
  checks_failed: "Checks failed", approved: "Approved", merged: "Live", closed: "Closed",
  question: "Question", answered: "Answered",
};
