import { FormEvent, useEffect, useState } from "react";
import { api, AppSettings, ServerTypeInfo } from "../api";

export default function SettingsPage() {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [types, setTypes] = useState<ServerTypeInfo[]>([]);
  const [pw, setPw] = useState({ current: "", next: "" });
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.settings(), api.serverTypes()])
      .then(([s, ty]) => {
        setSettings(s);
        setTypes(ty);
      })
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

  const changePassword = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await api.changePassword(pw.current, pw.next);
      setPw({ current: "", next: "" });
      setMsg("Password updated");
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  };

  if (!settings) {
    return <p className="muted">Loading settings…</p>;
  }

  return (
    <div className="stack">
      {msg && <div className="alert ok">{msg}</div>}
      {error && <div className="alert error">{error}</div>}

      <section className="card">
        <h2>General</h2>
        <form className="form-grid" onSubmit={saveSettings}>
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
                setSettings({ ...settings, poll_interval_seconds: Number(e.target.value) })
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
      </section>

      <section className="card">
        <h2>Change admin password</h2>
        <form className="form-grid" onSubmit={changePassword}>
          <label>
            Current password
            <input
              type="password"
              value={pw.current}
              onChange={(e) => setPw({ ...pw, current: e.target.value })}
              required
            />
          </label>
          <label>
            New password
            <input
              type="password"
              value={pw.next}
              onChange={(e) => setPw({ ...pw, next: e.target.value })}
              required
              minLength={6}
            />
          </label>
          <div className="full">
            <button className="btn primary">Update password</button>
          </div>
        </form>
      </section>

      {types.map((t) => {
        const typeSettings = settings.types?.[t.id] || { preferred_gamemode: "" };
        if (!t.features.map_travel) return null;
        return (
          <section className="card" key={t.id}>
            <h2>{t.label}</h2>
            <p className="muted">
              Type defaults for {t.label}. Quick RCON buttons are fixed per server type (not
              configurable).
            </p>
            <form
              className="form-grid"
              onSubmit={(e) => {
                e.preventDefault();
                saveSettings(e);
              }}
            >
              <label className="full">
                Preferred gamemode key
                <input
                  value={typeSettings.preferred_gamemode}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      types: {
                        ...settings.types,
                        [t.id]: {
                          ...typeSettings,
                          preferred_gamemode: e.target.value,
                        },
                      },
                    })
                  }
                />
              </label>
              <div className="full">
                <button className="btn primary" type="submit">
                  Save type settings
                </button>
              </div>
            </form>
            {t.quick_buttons?.length > 0 && (
              <div style={{ marginTop: "1rem" }}>
                <h3>Built-in quick RCON buttons</h3>
                <ul className="muted" style={{ margin: "0.5rem 0 0", paddingLeft: "1.25rem" }}>
                  {t.quick_buttons.map((b) => (
                    <li key={b.command}>
                      <strong>{b.label}</strong> - <code>{b.command}</code>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
