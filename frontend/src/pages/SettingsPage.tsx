import { FormEvent, useEffect, useState } from "react";
import { api, AppSettings } from "../api";
import { useAuth } from "../auth";
import MailSettingsPanel from "../components/MailSettingsPanel";

type Tab = "general" | "email";

export default function SettingsPage() {
  // Everyone can read the general settings (the dashboard needs the poll
  // interval); only administrators can change them, and only administrators
  // see the email tab at all. Your own password lives on /account.
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

  if (!settings) {
    return <p className="muted">Loading settings…</p>;
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: "general", label: "General" },
    ...(isAdmin ? [{ id: "email" as Tab, label: "Email" }] : []),
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
