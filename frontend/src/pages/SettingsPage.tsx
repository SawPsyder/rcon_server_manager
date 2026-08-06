import { FormEvent, useEffect, useState } from "react";
import { api, AppSettings, ButtonDraft, CustomButton, ServerTypeInfo } from "../api";

export default function SettingsPage() {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [types, setTypes] = useState<ServerTypeInfo[]>([]);
  const [typeButtons, setTypeButtons] = useState<Record<string, ButtonDraft[]>>({});
  const [pw, setPw] = useState({ current: "", next: "" });
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.settings(), api.serverTypes()])
      .then(async ([s, ty]) => {
        setSettings(s);
        setTypes(ty);
        const buttons: Record<string, ButtonDraft[]> = {};
        await Promise.all(
          ty.map(async (t) => {
            const btns = await api.buttons(t.id);
            buttons[t.id] = btns.map((b: CustomButton) => ({
              label: b.label,
              command: b.command,
              sort_order: b.sort_order,
            }));
          })
        );
        setTypeButtons(buttons);
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

  const saveTypeButtons = async (typeId: string) => {
    try {
      const drafts = (typeButtons[typeId] || [])
        .filter((b) => b.label.trim() && b.command.trim())
        .map((b, i) => ({
          label: b.label.trim(),
          command: b.command.trim(),
          sort_order: i,
        }));
      const updated = await api.replaceTypeButtons(typeId, drafts);
      setTypeButtons((prev) => ({
        ...prev,
        [typeId]: updated.map((b) => ({
          label: b.label,
          command: b.command,
          sort_order: b.sort_order,
        })),
      }));
      setMsg(`Quick buttons saved for ${typeId}`);
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
        const drafts = typeButtons[t.id] || [];
        return (
          <section className="card" key={t.id}>
            <h2>{t.label}</h2>
            <p className="muted">Type defaults shared by all {t.label} servers (unless overridden per server).</p>
            <form
              className="form-grid"
              onSubmit={(e) => {
                e.preventDefault();
                saveSettings(e);
              }}
            >
              {t.features.map_travel && (
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
              )}
              {t.features.map_travel && (
                <div className="full">
                  <button className="btn primary" type="submit">
                    Save type settings
                  </button>
                </div>
              )}
            </form>

            <h3 style={{ marginTop: "1.25rem" }}>Default quick RCON buttons</h3>
            <div className="stack">
              {drafts.map((btn, idx) => (
                <div key={idx} className="form-grid compact">
                  <label>
                    Label
                    <input
                      value={btn.label}
                      onChange={(e) => {
                        const next = [...drafts];
                        next[idx] = { ...next[idx], label: e.target.value };
                        setTypeButtons({ ...typeButtons, [t.id]: next });
                      }}
                    />
                  </label>
                  <label>
                    Command
                    <input
                      value={btn.command}
                      onChange={(e) => {
                        const next = [...drafts];
                        next[idx] = { ...next[idx], command: e.target.value };
                        setTypeButtons({ ...typeButtons, [t.id]: next });
                      }}
                    />
                  </label>
                  <div className="row">
                    <button
                      className="btn small danger"
                      type="button"
                      onClick={() =>
                        setTypeButtons({
                          ...typeButtons,
                          [t.id]: drafts.filter((_, i) => i !== idx),
                        })
                      }
                    >
                      Remove
                    </button>
                  </div>
                </div>
              ))}
              <div className="row">
                <button
                  type="button"
                  className="btn small"
                  onClick={() =>
                    setTypeButtons({
                      ...typeButtons,
                      [t.id]: [...drafts, { label: "", command: "" }],
                    })
                  }
                >
                  Add button
                </button>
                <button
                  type="button"
                  className="btn primary"
                  onClick={() => saveTypeButtons(t.id)}
                >
                  Save buttons
                </button>
              </div>
            </div>
          </section>
        );
      })}
    </div>
  );
}
