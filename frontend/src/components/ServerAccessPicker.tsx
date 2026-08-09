import { useMemo, useState } from "react";
import type { Server, ServerTypeInfo } from "../api";

/**
 * Picks which servers a user may operate.
 *
 * A wrapping cloud of checkbox pills stopped working past a handful of
 * servers: no way to tell how many were selected, no way to find one by name,
 * and the rows reflowed every time the window changed width. This is a real
 * list instead - searchable, countable, and each row says what the server is.
 */
export default function ServerAccessPicker({
  servers,
  types,
  selected,
  onChange,
  disabled = false,
}: {
  servers: Server[];
  types: ServerTypeInfo[];
  selected: number[];
  onChange: (ids: number[]) => void;
  disabled?: boolean;
}) {
  const [query, setQuery] = useState("");

  const typeLabels = useMemo(() => {
    const map = new Map<string, string>();
    for (const t of types) map.set(t.id, t.label);
    return map;
  }, [types]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return servers;
    return servers.filter((s) => {
      const type = typeLabels.get(s.server_type) ?? s.server_type;
      return (
        s.name.toLowerCase().includes(q) ||
        type.toLowerCase().includes(q) ||
        s.host.toLowerCase().includes(q)
      );
    });
  }, [servers, query, typeLabels]);

  const toggle = (id: number) =>
    onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);

  // Select all / clear act on what is currently visible, so a filtered search
  // followed by "Select all" does not silently grant the servers you filtered
  // out - and does not drop the ones already granted outside the filter.
  const visibleIds = filtered.map((s) => s.id);
  const allVisibleSelected =
    visibleIds.length > 0 && visibleIds.every((id) => selected.includes(id));

  const selectAllVisible = () =>
    onChange(Array.from(new Set([...selected, ...visibleIds])));
  const clearVisible = () => onChange(selected.filter((id) => !visibleIds.includes(id)));

  if (servers.length === 0) {
    return (
      <p className="muted">
        No servers have been added yet. Add one under Servers, then grant access here.
      </p>
    );
  }

  const showSearch = servers.length > 5;

  return (
    <div className={`access-picker${disabled ? " is-disabled" : ""}`}>
      <div className="row between wrap access-picker-head">
        <span className="muted">
          {selected.length === 0
            ? "No servers selected"
            : `${selected.length} of ${servers.length} selected`}
        </span>
        <div className="row">
          <button
            type="button"
            className="btn ghost small"
            disabled={disabled || allVisibleSelected}
            onClick={selectAllVisible}
          >
            {query ? "Select matching" : "Select all"}
          </button>
          <button
            type="button"
            className="btn ghost small"
            disabled={disabled || !visibleIds.some((id) => selected.includes(id))}
            onClick={clearVisible}
          >
            {query ? "Clear matching" : "Clear"}
          </button>
        </div>
      </div>

      {showSearch && (
        <input
          type="search"
          className="access-picker-search"
          placeholder="Find a server"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          disabled={disabled}
        />
      )}

      <div className="access-picker-list" role="group" aria-label="Server access">
        {filtered.length === 0 ? (
          <p className="muted access-picker-empty">No servers match "{query}".</p>
        ) : (
          filtered.map((s) => {
            const checked = selected.includes(s.id);
            return (
              <label
                key={s.id}
                className={`access-row${checked ? " is-checked" : ""}`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={disabled}
                  onChange={() => toggle(s.id)}
                />
                <span className="access-row-main">
                  <span className="access-row-name">{s.name}</span>
                  <span className="muted access-row-meta">
                    {typeLabels.get(s.server_type) ?? s.server_type}
                    {s.last_online === false && " · offline"}
                  </span>
                </span>
              </label>
            );
          })
        )}
      </div>
    </div>
  );
}
