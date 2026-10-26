"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, setApiUser, type Area, type ClientInfo, type Person } from "./api";

type ScreenContext = Record<string, unknown>;

type Studio = {
  people: Person[]; areas: Area[]; clients: ClientInfo[];
  user: string; setUser: (id: string) => void; me: Person | undefined;
  client: string; setClient: (name: string) => void; clientInfo: ClientInfo | undefined;
  counts: Record<string, number>; refreshCounts: () => void;
  screen: ScreenContext; setScreen: (s: ScreenContext) => void;
  panelOpen: boolean; setPanelOpen: (open: boolean) => void;
  version: number; bump: () => void;   // bump after a write so open screens refetch
  error: string | null;
};

const Ctx = createContext<Studio | null>(null);

function stored(key: string, fallback: string) {
  if (typeof window === "undefined") return fallback;
  try { return window.localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}

export function StudioProvider({ children }: { children: React.ReactNode }) {
  const [people, setPeople] = useState<Person[]>([]);
  const [areas, setAreas] = useState<Area[]>([]);
  const [clients, setClients] = useState<ClientInfo[]>([]);
  const [user, setUserState] = useState("");
  const [client, setClientState] = useState("");
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [screen, setScreen] = useState<ScreenContext>({});
  const [panelOpen, setPanelOpenState] = useState(true);
  const [version, setVersion] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.people(), api.clients()]).then(([p, c]) => {
      setPeople(p.people); setAreas(p.areas); setClients(c.clients);
      const u = stored("studio.user", "pack-steward");
      const cl = stored("studio.client", c.default);
      setApiUser(u); setUserState(u);
      setClientState(c.clients.some((x) => x.name === cl) ? cl : c.default);
      setPanelOpenState(stored("studio.panel", "1") === "1");
    }).catch((e) => setError(`Cannot reach the Studio API (${e.message}). Start it with: ctx-studio serve`));
  }, []);

  const setUser = useCallback((id: string) => {
    setApiUser(id); setUserState(id);
    try { window.localStorage.setItem("studio.user", id); } catch { /* private window */ }
    setVersion((v) => v + 1);
  }, []);
  const setClient = useCallback((name: string) => {
    setClientState(name);
    try { window.localStorage.setItem("studio.client", name); } catch { /* private window */ }
    setVersion((v) => v + 1);
  }, []);
  const setPanelOpen = useCallback((open: boolean) => {
    setPanelOpenState(open);
    try { window.localStorage.setItem("studio.panel", open ? "1" : "0"); } catch { /* private window */ }
  }, []);

  const refreshCounts = useCallback(() => {
    if (!client) return;
    api.inbox(client, "mine").then((r) => setCounts(r.counts)).catch(() => {});
  }, [client]);
  useEffect(() => { refreshCounts(); }, [refreshCounts, user, version]);
  useEffect(() => {
    const t = setInterval(refreshCounts, 8000);
    return () => clearInterval(t);
  }, [refreshCounts]);

  const value = useMemo<Studio>(() => ({
    people, areas, clients, user, setUser, me: people.find((p) => p.id === user),
    client, setClient, clientInfo: clients.find((c) => c.name === client),
    counts, refreshCounts, screen, setScreen, panelOpen, setPanelOpen,
    version, bump: () => setVersion((v) => v + 1), error,
  }), [people, areas, clients, user, setUser, client, setClient, counts, refreshCounts, screen, panelOpen, setPanelOpen, version, error]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStudio() {
  const s = useContext(Ctx);
  if (!s) throw new Error("useStudio outside StudioProvider");
  return s;
}

export function personName(people: Person[], id: string) {
  return people.find((p) => p.id === id)?.name ?? id;
}
