import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, AppSettings } from "../api";
import { useAuth } from "../auth";
import ClientIpHelpersPanel from "../components/ClientIpHelpersPanel";
import MailSettingsPanel from "../components/MailSettingsPanel";
import PterodactylSettingsPanel from "../components/PterodactylSettingsPanel";

type Tab = "general" | "email" | "pterodactyl" | "helpers";

/** Common IANA zones for the app schedule clock. Select (not free-text) so it
 *  matches every other settings control and cannot fight the user mid-edit. */
const APP_TIMEZONES = [
  "UTC",
  "Europe/Amsterdam",
  "Europe/Berlin",
  "Europe/Brussels",
  "Europe/Dublin",
  "Europe/Helsinki",
  "Europe/Lisbon",
  "Europe/London",
  "Europe/Madrid",
  "Europe/Moscow",
  "Europe/Oslo",
  "Europe/Paris",
  "Europe/Prague",
  "Europe/Rome",
  "Europe/Stockholm",
  "Europe/Vienna",
  "Europe/Warsaw",
  "Europe/Zurich",
  "Africa/Cairo",
  "Africa/Johannesburg",
  "America/Anchorage",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "America/Mexico_City",
  "America/New_York",
  "America/Sao_Paulo",
  "America/Toronto",
  "America/Vancouver",
  "Asia/Bangkok",
  "Asia/Dubai",
  "Asia/Hong_Kong",
  "Asia/Jakarta",
  "Asia/Kolkata",
  "Asia/Seoul",
  "Asia/Shanghai",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Melbourne",
  "Australia/Perth",
  "Australia/Sydney",
  "Pacific/Auckland",
  "Pacific/Honolulu",
] as const;

export default function SettingsPage() {
  // Everyone can read the general settings (the dashboard needs the poll
  // interval); only administrators can change them, and only administrators
  // see the email and helpers tabs. Your own password lives on /account.
  const { isAdmin, reloadConfig } = useAuth();
  const [tab, setTab] = useState<Tab>("general");
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .settings()
      .then(setSettings)
      .catch((e) => setError(String(e)));
  }, []);

  const saveSettings = async (e: FormEvent) => {
    e.preventDefault();
    if (!settings) return;
    try {
      const s = await api.updateSettings(settings);
      setSettings(s);
      setMsg("Settings saved");
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  };

  const timezoneOptions = useMemo(() => {
    const current = (settings?.app_timezone || "UTC").trim() || "UTC";
    // Keep a stored value that is not in the curated list selectable.
    if (APP_TIMEZONES.includes(current as (typeof APP_TIMEZONES)[number])) {
      return [...APP_TIMEZONES];
    }
    return [current, ...APP_TIMEZONES];
  }, [settings?.app_timezone]);

  if (!settings) {
    return <p className="muted">Loading settings…</p>;
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: "general", label: "General" },
    ...(isAdmin
      ? [
          { id: "email" as Tab, label: "Email" },
          { id: "pterodactyl" as Tab, label: "Pterodactyl" },
          { id: "helpers" as Tab, label: "Helpers" },
        ]
      : []),
  ];

  return (
    <div className="stack">
      {tabs.length > 1 && (
        <div className="row wrap">
          {tabs.map((t) => (
            <button
              key={t.id}
              className={`btn small ${tab === t.id ? "primary" : "ghost"}`}
              onClick={() => {
                setTab(t.id);
                setMsg("");
                setError("");
              }}
            >
              {t.label}
            </button>
          ))}
        </div>
      )}

      {msg && <div className="alert ok">{msg}</div>}
      {error && <div className="alert error">{error}</div>}

      {tab === "email" && isAdmin && (
        <MailSettingsPanel onSaved={() => void reloadConfig()} />
      )}

      {tab === "pterodactyl" && isAdmin && <PterodactylSettingsPanel />}

      {tab === "helpers" && isAdmin && <ClientIpHelpersPanel />}

      {tab === "general" && (
        <>
          {!isAdmin && (
            <div className="alert">
              These are read-only. Ask an administrator to change them.
            </div>
          )}

          <section className="card">
            <h2>General</h2>
            <fieldset
              disabled={!isAdmin}
              style={{ border: 0, margin: 0, padding: 0 }}
            >
              <form className="form-grid settings-form" onSubmit={saveSettings}>
                <label>
                  Query timeout (s)
                  <input
                    type="number"
                    step="0.1"
                    value={settings.query_timeout}
                    onChange={(e) =>
                      setSettings({ ...settings, query_timeout: Number(e.target.value) })
                    }
                  />
                </label>
                <label>
                  Dashboard auto-refresh (s)
                  <input
                    type="number"
                    value={settings.poll_interval_seconds}
                    onChange={(e) =>
                      setSettings({
                        ...settings,
                        poll_interval_seconds: Number(e.target.value),
                      })
                    }
                  />
                </label>
                <label>
                  Player stats sample interval (s)
                  <input
                    type="number"
                    min={15}
                    value={settings.stats_interval_seconds}
                    onChange={(e) =>
                      setSettings({
                        ...settings,
                        stats_interval_seconds: Number(e.target.value),
                      })
                    }
                  />
                </label>
                <label className="full">
                  App timezone
                  <select
                    value={settings.app_timezone || "UTC"}
                    onChange={(e) =>
                      setSettings({
                        ...settings,
                        app_timezone: e.target.value,
                      })
                    }
                  >
                    {timezoneOptions.map((z) => (
                      <option key={z} value={z}>
                        {z}
                      </option>
                    ))}
                  </select>
                  <span className="muted" style={{ fontSize: "0.85rem" }}>
                    Used for all server schedules (e.g. 04:00 means 04:00 in this
                    timezone).
                  </span>
                </label>
                <div className="full">
                  <button className="btn primary">Save settings</button>
                </div>
              </form>
            </fieldset>
          </section>
        </>
      )}
    </div>
  );
}
