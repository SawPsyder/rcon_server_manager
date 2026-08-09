import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";

/**
 * Cloudflare Turnstile widget.
 *
 * Renders nothing when Turnstile is not configured on the backend, so a
 * self-hosted install with no site key keeps a plain login form.
 *
 * Tokens are single-use. This SPA never navigates away on submit - a failed
 * login just renders an inline alert - so a naive retry would re-send the
 * already-redeemed token and Cloudflare would reject it as
 * `timeout-or-duplicate`. Every caller must therefore call `reset()` on the
 * ref in its failure path; that is what `TurnstileHandle` exists for.
 */

const SCRIPT_URL =
  "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

type TurnstileApi = {
  render: (el: HTMLElement, opts: Record<string, unknown>) => string;
  reset: (id?: string) => void;
  remove: (id?: string) => void;
};

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

let scriptPromise: Promise<void> | null = null;

/** Load the Turnstile script once, lazily, and only when it is actually used. */
function loadScript(): Promise<void> {
  if (window.turnstile) return Promise.resolve();
  if (scriptPromise) return scriptPromise;

  scriptPromise = new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = SCRIPT_URL;
    script.async = true;
    script.defer = true;
    script.onload = () => resolve();
    script.onerror = () => {
      scriptPromise = null;
      reject(new Error("Could not load the Cloudflare Turnstile script"));
    };
    document.head.appendChild(script);
  });
  return scriptPromise;
}

export type TurnstileHandle = {
  /** Discard the current token and request a fresh one. Call after any failure. */
  reset: () => void;
};

type Props = {
  siteKey: string;
  onToken: (token: string) => void;
};

const TurnstileWidget = forwardRef<TurnstileHandle, Props>(function TurnstileWidget(
  { siteKey, onToken },
  ref,
) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const widgetIdRef = useRef<string | null>(null);
  const onTokenRef = useRef(onToken);
  const [error, setError] = useState("");

  // Keep the latest callback without re-rendering the widget on every keystroke
  // in the parent form.
  useEffect(() => {
    onTokenRef.current = onToken;
  }, [onToken]);

  useImperativeHandle(ref, () => ({
    reset: () => {
      onTokenRef.current("");
      if (window.turnstile && widgetIdRef.current !== null) {
        window.turnstile.reset(widgetIdRef.current);
      }
    },
  }));

  useEffect(() => {
    let cancelled = false;

    loadScript()
      .then(() => {
        if (cancelled || !containerRef.current || !window.turnstile) return;
        if (widgetIdRef.current !== null) return;
        widgetIdRef.current = window.turnstile.render(containerRef.current, {
          sitekey: siteKey,
          action: "turnstile-spin-v2",
          callback: (token: string) => {
            setError("");
            onTokenRef.current(token);
          },
          "error-callback": () => {
            onTokenRef.current("");
            setError("Verification could not be completed. Please try again.");
          },
          "expired-callback": () => {
            // The token went stale before submit; force a fresh one.
            onTokenRef.current("");
            if (window.turnstile && widgetIdRef.current !== null) {
              window.turnstile.reset(widgetIdRef.current);
            }
          },
        });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Verification unavailable");
        }
      });

    return () => {
      cancelled = true;
      if (window.turnstile && widgetIdRef.current !== null) {
        try {
          window.turnstile.remove(widgetIdRef.current);
        } catch {
          /* widget already gone */
        }
        widgetIdRef.current = null;
      }
    };
  }, [siteKey]);

  return (
    <div className="full">
      {/* data-action is required on every widget this integration renders. */}
      <div
        ref={containerRef}
        className="cf-turnstile"
        data-sitekey={siteKey}
        data-action="turnstile-spin-v2"
      />
      {error && <div className="alert error">{error}</div>}
    </div>
  );
});

export default TurnstileWidget;
