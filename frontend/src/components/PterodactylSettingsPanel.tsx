import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, type PterodactylSettings } from "../api";

const BLANK: PterodactylSettings = {
  base_url: "",
  has_api_key: false,
  verify_tls: true,
  enabled: false,
};

export default function PterodactylSettingsPanel({ onSaved }: { onSaved?: () => void }) {
  const [form, setForm] = useState<PterodactylSettings>(BLANK);
  const [loaded, setLoaded] = useState(false);
  /** Left blank to keep the stored key; the server never sends it back. */
  const [apiKey, setApiKey] = useState("");
  const [clearApiKey, setClearApiKey] = useState(false);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setForm(await api.pterodactyl.get());
      setError("");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not load Pterodactyl settings",
      );
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const set = <K extends keyof PterodactylSettings>(
    key: K,
    value: PterodactylSettings[K],
  ) => setForm((f) => ({ ...f, [key]: value }));

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg("");
    setError("");
    try {
      const next = await api.pterodactyl.update({
        base_url: form.base_url,
        // undefined keeps the stored key; "" clears it.
        api_key: clearApiKey ? "" : apiKey ? apiKey : undefined,
        verify_tls: form.verify_tls,
      });
      setForm(next);
      setApiKey("");
      setClearApiKey(false);
      setMsg("Pterodactyl settings saved.");
      onSaved?.();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not save Pterodactyl settings",
      );
    } finally {
      setBusy(false);
    }
  };

  const testConnection = async () => {
    setTesting(true);
    setMsg("");
    setError("");
    try {
      const result = await api.pterodactyl.test();
      setMsg(result.detail);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach the panel");
    } finally {
      setTesting(false);
    }
  };

  if (!loaded) {
    return <p className="muted">Loading Pterodactyl settings…</p>;
  }

  // A stored key that will not decrypt: ENCRYPTION_KEY changed under it.
  // decrypt_secret warns and returns "" rather than raising, so without this
  // the panel would just look mysteriously offline.
  const keyUndecryptable = form.has_api_key && !!form.base_url && !form.enabled;

  return (
    <div className="stack">
      {msg && <div className="alert ok">{msg}</div>}
      {error && <div className="alert error">{error}</div>}

      <section className="card">
        <div className="row between wrap">
          <div>
            <h2>Pterodactyl panel</h2>
            <p className="muted" style={{ margin: 0 }}>
              Reads container CPU, memory and disk, and adds start / stop /
              restart to any server you link. Provisioning and schedules stay in
              the panel - this only watches and signals.
            </p>
          </div>
          <span className={`pill ${form.enabled ? "online" : "offline"}`}>
            {form.enabled ? "Configured" : "Not configured"}
          </span>
        </div>

        {keyUndecryptable && (
          <div className="alert error">
            An API key is stored but could not be decrypted. This happens when
            ENCRYPTION_KEY changes - re-enter the key below.
          </div>
        )}

        <form className="form-grid settings-form" onSubmit={save}>
          <label className="full">
            Panel URL
            <input
              type="url"
              placeholder="https://panel.example.com"
              value={form.base_url}
              onChange={(e) => set("base_url", e.target.value)}
            />
            <span className="muted" style={{ fontSize: "0.8rem" }}>
              The panel's own address, with no path. A trailing <code>/api</code>{" "}
              is removed for you. Leave empty to turn the integration off.
            </span>
          </label>

          {/* The "remove" checkbox is a sibling, not a child, of the key label -
              nesting one label inside another is invalid and makes the click
              target ambiguous. */}
          <div className="field full">
            <label htmlFor="ptero-api-key">
              Client API key{" "}
              {form.has_api_key && !clearApiKey ? "(leave blank to keep)" : ""}
            </label>
            <input
              id="ptero-api-key"
              type="password"
              autoComplete="new-password"
              disabled={clearApiKey}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={form.has_api_key ? "••••••••" : "ptlc_…"}
            />
            <span className="muted" style={{ fontSize: "0.8rem" }}>
              Create it in the panel under <strong>Account Settings → API
              Credentials</strong>. An <em>Application</em> key from the admin
              area will not work: it has no resource or power endpoints, however
              much access it carries.
            </span>
            {form.has_api_key && (
              <label className="inline" style={{ fontSize: "0.8rem" }}>
                <input
                  type="checkbox"
                  checked={clearApiKey}
                  onChange={(e) => {
                    setClearApiKey(e.target.checked);
                    if (e.target.checked) setApiKey("");
                  }}
                />
                Remove the stored key
              </label>
            )}
          </div>

          <div className="field full">
            <label className="inline">
              <input
                type="checkbox"
                checked={form.verify_tls}
                onChange={(e) => set("verify_tls", e.target.checked)}
              />
              Verify the panel's TLS certificate
            </label>
            <span className="muted" style={{ fontSize: "0.8rem" }}>
              Leave this on unless the panel uses a self-signed certificate.
              Turning it off sends the API key to whatever answers that address.
            </span>
          </div>

          <div className="full row wrap">
            <button className="btn primary" disabled={busy}>
              {busy ? "Saving…" : "Save panel settings"}
            </button>
            <button
              type="button"
              className="btn ghost"
              disabled={testing || busy || !form.base_url}
              title={
                !form.base_url
                  ? "Enter the panel URL first"
                  : "Contacts the panel using the saved settings (save changes first)"
              }
              onClick={() => void testConnection()}
            >
              {testing ? "Testing…" : "Test connection"}
            </button>
          </div>
        </form>
      </section>

      <section className="card">
        <h2>Linking servers</h2>
        <p className="muted" style={{ margin: 0 }}>
          Once this connects, open <strong>Servers</strong> and pick a panel
          container for each server. Linked servers gain a resources card and
          power controls on their detail page, and their CPU and memory are
          recorded alongside the player-count history.
        </p>
      </section>
    </div>
  );
}
