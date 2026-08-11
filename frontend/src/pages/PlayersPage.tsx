import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  api,
  identityKey,
  parseIdentity,
  PlayerLeaderboard,
  PlayerLeaderboardRow,
  PlayerLeaderboardSort,
  Server,
} from "../api";
import IdentityDossierModal from "../components/IdentityDossierModal";
import IdentityInfoButton from "../components/IdentityInfoButton";

const PAGE_SIZE = 50;
const SORT_OPTIONS: { value: PlayerLeaderboardSort; label: string }[] = [
  { value: "total_seconds", label: "Playtime" },
  { value: "last_seen_at", label: "Last seen" },
  { value: "name", label: "Name" },
  { value: "visit_count", label: "Visits" },
];

function platformLabel(platform: string): string {
  const known: Record<string, string> = {
    steam: "Steam",
    xbox: "Xbox",
    psn: "PlayStation",
    eos: "Epic",
    mac: "Mac",
    unknown: "Unknown",
  };
  return known[platform] || platform;
}

export default function PlayersPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [servers, setServers] = useState<Server[]>([]);
  const [data, setData] = useState<PlayerLeaderboard | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [identityFlags, setIdentityFlags] = useState<Record<string, boolean>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [dossierOpen, setDossierOpen] = useState(false);
  const [dossierNetId, setDossierNetId] = useState("");
  const [dossierName, setDossierName] = useState("");
  /** Draft for the inline page-number input (committed on Enter/blur). */
  const [pageDraft, setPageDraft] = useState("");

  // Local draft for search so typing does not hammer the API until debounce/enter.
  const qParam = searchParams.get("q") || "";
  const [qDraft, setQDraft] = useState(qParam);
  const serverIdParam = searchParams.get("server") || "";
  const sortParam = (searchParams.get("sort") || "total_seconds") as PlayerLeaderboardSort;
  const orderParam = searchParams.get("order") === "asc" ? "asc" : "desc";
  const pageParam = Math.max(1, parseInt(searchParams.get("page") || "1", 10) || 1);

  useEffect(() => {
    setQDraft(qParam);
  }, [qParam]);

  const setParam = useCallback(
    (key: string, value: string | null, resetPage = true) => {
      const next = new URLSearchParams(searchParams);
      if (value) next.set(key, value);
      else next.delete(key);
      if (resetPage && key !== "page") next.delete("page");
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const loadServers = useCallback(async () => {
    try {
      setServers(await api.listServers());
    } catch {
      /* filter still works with empty list */
    }
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const serverId = serverIdParam ? parseInt(serverIdParam, 10) : null;
      const result = await api.listPlayers({
        q: qParam || undefined,
        serverId: Number.isFinite(serverId as number) && (serverId as number) > 0 ? serverId : null,
        sort: SORT_OPTIONS.some((s) => s.value === sortParam) ? sortParam : "total_seconds",
        order: orderParam,
        page: pageParam,
        pageSize: PAGE_SIZE,
      });
      setData(result);
      setError("");

      const identities = result.players
        .map((p) => ({ platform: p.platform, external_id: p.external_id, net_id: p.net_id }))
        .filter((p) => p.platform && p.external_id);
      if (identities.length) {
        try {
          const flags = await api.identityFlags(identities);
          setIdentityFlags(flags.flags || {});
        } catch {
          setIdentityFlags({});
        }
      } else {
        setIdentityFlags({});
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [qParam, serverIdParam, sortParam, orderParam, pageParam]);

  useEffect(() => {
    loadServers();
  }, [loadServers]);

  useEffect(() => {
    load();
  }, [load]);

  // Debounce search typing into the URL (and thus the load effect).
  useEffect(() => {
    if (qDraft === qParam) return;
    const t = window.setTimeout(() => {
      setParam("q", qDraft.trim() || null);
    }, 300);
    return () => window.clearTimeout(t);
  }, [qDraft, qParam, setParam]);

  const openDossier = (netId: string, name?: string) => {
    if (!parseIdentity(netId)) return;
    setDossierNetId(netId);
    setDossierName(name || "");
    setDossierOpen(true);
  };

  const rowKey = (p: PlayerLeaderboardRow) => `${p.platform}:${p.external_id}`;

  const totalPages = useMemo(() => {
    if (!data) return 1;
    return Math.max(1, Math.ceil(data.total / data.page_size));
  }, [data]);

  // Keep the page input in sync when navigation or filters change the page.
  useEffect(() => {
    if (data) setPageDraft(String(data.page));
  }, [data?.page, data?.total, totalPages]);

  const goToPage = (raw: string) => {
    const n = parseInt(raw.trim(), 10);
    if (!Number.isFinite(n)) {
      setPageDraft(String(data?.page ?? pageParam));
      return;
    }
    const clamped = Math.min(totalPages, Math.max(1, n));
    setPageDraft(String(clamped));
    if (clamped !== pageParam) {
      setParam("page", clamped === 1 ? null : String(clamped), false);
    }
  };

  const filteredServer = useMemo(() => {
    if (!serverIdParam) return null;
    const id = parseInt(serverIdParam, 10);
    return servers.find((s) => s.id === id) || null;
  }, [serverIdParam, servers]);

  const toggleSort = (sort: PlayerLeaderboardSort) => {
    const next = new URLSearchParams(searchParams);
    if (sortParam === sort) {
      next.set("order", orderParam === "desc" ? "asc" : "desc");
    } else {
      next.set("sort", sort);
      next.set("order", sort === "name" ? "asc" : "desc");
    }
    next.delete("page");
    setSearchParams(next, { replace: true });
  };

  return (
    <div className="players-page">
      <section className="card">
        <div className="page-header">
          <div>
            <h1>Players</h1>
            <p className="muted" style={{ margin: "0.25rem 0 0" }}>
              Everyone tracked on your servers by playtime
              {filteredServer ? (
                <>
                  {" "}
                  · ranked on <strong>{filteredServer.name}</strong>
                </>
              ) : (
                <> · overall rank is total time across all servers</>
              )}
            </p>
          </div>
          <button type="button" className="btn ghost" onClick={() => load()} disabled={loading}>
            Refresh
          </button>
        </div>

        <div className="players-toolbar">
          <label className="field">
            <span>Search</span>
            <input
              type="search"
              value={qDraft}
              placeholder="Name or player ID"
              onChange={(e) => setQDraft(e.target.value)}
            />
          </label>
          <label className="field">
            <span>Server</span>
            <select
              value={serverIdParam}
              onChange={(e) => setParam("server", e.target.value || null)}
            >
              <option value="">All servers</option>
              {servers.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Sort</span>
            <select
              value={sortParam}
              onChange={(e) => {
                const sort = e.target.value as PlayerLeaderboardSort;
                const next = new URLSearchParams(searchParams);
                next.set("sort", sort);
                if (!next.get("order")) {
                  next.set("order", sort === "name" ? "asc" : "desc");
                }
                next.delete("page");
                setSearchParams(next, { replace: true });
              }}
            >
              {SORT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Order</span>
            <select
              value={orderParam}
              onChange={(e) => setParam("order", e.target.value)}
            >
              <option value="desc">Descending</option>
              <option value="asc">Ascending</option>
            </select>
          </label>
        </div>

        {error && <div className="alert error">{error}</div>}

        <div className="table-wrap">
          <table className="players-table">
            <thead>
              <tr>
                <th
                  title="Place by playtime (overall, or on the filtered server)"
                  className="sortable"
                  onClick={() => toggleSort("total_seconds")}
                >
                  Rank
                </th>
                <th className="sortable" onClick={() => toggleSort("name")}>
                  Name
                </th>
                <th>Platform</th>
                <th
                  className="sortable"
                  onClick={() => toggleSort("total_seconds")}
                  title={
                    filteredServer
                      ? "Playtime on the selected server"
                      : "Sum of playtime across all granted servers"
                  }
                >
                  {filteredServer ? "Server time" : "Total time"}
                </th>
                <th className="sortable" onClick={() => toggleSort("visit_count")}>
                  Visits
                </th>
                <th className="sortable" onClick={() => toggleSort("last_seen_at")}>
                  Last seen
                </th>
                <th title="Servers this player has time on">Servers</th>
                <th>Player ID</th>
              </tr>
            </thead>
            <tbody>
              {loading && !data ? (
                <tr className="no-row-click">
                  <td colSpan={8} className="muted">
                    Loading…
                  </td>
                </tr>
              ) : !data || data.players.length === 0 ? (
                <tr className="no-row-click">
                  <td colSpan={8} className="muted">
                    No tracked players yet
                    {qParam ? " matching this search" : ""}
                    {filteredServer ? ` on ${filteredServer.name}` : ""}.
                  </td>
                </tr>
              ) : (
                data.players.map((p) => {
                  const key = rowKey(p);
                  const isOpen = Boolean(expanded[key]);
                  const flagKey = identityKey(p.platform, p.external_id);
                  const hasInfo = Boolean(identityFlags[flagKey]);
                  return (
                    <Fragment key={key}>
                      <tr
                        className={isOpen ? "selected" : undefined}
                        onClick={() =>
                          setExpanded((prev) => ({ ...prev, [key]: !prev[key] }))
                        }
                      >
                        <td>
                          <span
                            className={`rank-badge ${
                              p.rank === 1
                                ? "gold"
                                : p.rank === 2
                                  ? "silver"
                                  : p.rank === 3
                                    ? "bronze"
                                    : ""
                            }`}
                          >
                            {p.rank != null && p.ranked_players > 0
                              ? `${p.rank}/${p.ranked_players}`
                              : "-"}
                          </span>
                        </td>
                        <td>
                          <span className="name-with-info">
                            <span>{p.display_name}</span>
                            {p.net_id ? (
                              <IdentityInfoButton
                                hasInfo={hasInfo}
                                onClick={() => openDossier(p.net_id, p.display_name)}
                              />
                            ) : null}
                            {p.online && (
                              <span
                                className="pill online players-online-pill"
                                title={
                                  p.online_server_names.length
                                    ? `Online on ${p.online_server_names.join(", ")}`
                                    : "Online"
                                }
                              >
                                Online
                              </span>
                            )}
                          </span>
                        </td>
                        <td>
                          {(p.linked_identities && p.linked_identities.length > 1
                            ? p.linked_identities
                            : [{ platform: p.platform, external_id: p.external_id, net_id: p.net_id, last_name: "" }]
                          ).map((li) => (
                            <span
                              key={`${li.platform}:${li.external_id}`}
                              className={`platform-pill ${li.platform}`}
                              style={{ marginRight: "0.3rem" }}
                              title={li.net_id || li.external_id}
                            >
                              {platformLabel(li.platform)}
                            </span>
                          ))}
                        </td>
                        <td>
                          {p.total_pretty}
                          {filteredServer && p.overall_seconds !== p.total_seconds ? (
                            <span className="muted" title="Time across all your servers">
                              {" "}
                              (all: {p.overall_pretty})
                            </span>
                          ) : null}
                        </td>
                        <td>{p.visit_count}</td>
                        <td title={p.last_seen_at || undefined}>{p.last_seen_pretty}</td>
                        <td>
                          <span className="players-server-count">
                            {p.servers.length}
                            <span className="muted expand-hint">
                              {isOpen ? " ▲" : " ▼"}
                            </span>
                          </span>
                        </td>
                        <td>
                          <code className="steam-id">{p.net_id || p.external_id}</code>
                        </td>
                      </tr>
                      {isOpen && (
                        <tr className="players-detail-row no-row-click">
                          <td colSpan={8}>
                            <div className="players-detail">
                              <table>
                                <thead>
                                  <tr>
                                    <th>Server</th>
                                    <th>Rank</th>
                                    <th>Time</th>
                                    <th>Visits</th>
                                    <th>Last seen</th>
                                    <th>Name on server</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {p.servers.map((s) => (
                                    <tr key={s.server_id} className="no-row-click">
                                      <td>
                                        <Link to={`/server/${s.server_id}`}>{s.server_name}</Link>
                                        {s.online && (
                                          <span className="pill online players-online-pill">
                                            Online
                                          </span>
                                        )}
                                      </td>
                                      <td>
                                        {s.rank != null && s.ranked_players > 0
                                          ? `${s.rank}/${s.ranked_players}`
                                          : "-"}
                                      </td>
                                      <td>{s.total_pretty}</td>
                                      <td>{s.visit_count}</td>
                                      <td title={s.last_seen_at || undefined}>
                                        {s.last_seen_pretty}
                                      </td>
                                      <td>{s.last_name}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {data && data.total > 0 && (
          <div className="players-pager history-pager row gap">
            <button
              type="button"
              className="btn small ghost"
              disabled={pageParam <= 1 || loading}
              onClick={() => setParam("page", String(pageParam - 1), false)}
            >
              Previous
            </button>
            <span className="muted players-page-label">
              Page{" "}
              <input
                type="number"
                className="page-number-input"
                min={1}
                max={totalPages}
                step={1}
                value={pageDraft}
                disabled={loading}
                aria-label="Page number"
                title={`Go to page (1–${totalPages})`}
                onChange={(e) => setPageDraft(e.target.value)}
                onBlur={() => goToPage(pageDraft)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    (e.target as HTMLInputElement).blur();
                  } else if (e.key === "Escape") {
                    setPageDraft(String(data.page));
                    (e.target as HTMLInputElement).blur();
                  }
                }}
              />{" "}
              of {totalPages} · {data.total} player
              {data.total === 1 ? "" : "s"}
            </span>
            <button
              type="button"
              className="btn small ghost"
              disabled={pageParam >= totalPages || loading}
              onClick={() => setParam("page", String(pageParam + 1), false)}
            >
              Next
            </button>
          </div>
        )}
      </section>

      <IdentityDossierModal
        open={dossierOpen}
        netId={dossierNetId}
        fallbackName={dossierName}
        onClose={() => setDossierOpen(false)}
        onChanged={() => load()}
      />
    </div>
  );
}
