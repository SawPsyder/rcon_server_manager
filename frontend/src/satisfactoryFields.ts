/**
 * Widget metadata for the Satisfactory HTTPS API's key/value setting maps.
 *
 * `GetServerOptions` and `GetAdvancedGameSettings` both hand back flat maps of
 * opaque `FG.*` keys, so rendering them generically means a bare text box per
 * row and the operator guessing whether a toggle wants `True`, `true` or `1`.
 * The catalogues below turn the keys documented for 1.x into real controls.
 * Anything not in a catalogue is inferred from the value the server reported,
 * so a key added by a future game update still gets a usable control instead
 * of disappearing.
 *
 * Writing back is deliberately format-preserving. Server options are a
 * string map while advanced settings are JSON, and different keys arrive in
 * different shapes (`"True"`, `true`, `1`). `encodeOption` / `encodeAdvanced`
 * echo the shape the server used for that key rather than imposing one.
 */

export type SelectChoice = { value: string; label: string };

export type FieldSpec =
  | { kind: "bool"; label: string; help?: string }
  | { kind: "enum"; label: string; choices: SelectChoice[]; help?: string }
  | {
      kind: "number";
      label: string;
      min?: number;
      max?: number;
      step?: number;
      unit?: string;
      help?: string;
    }
  | { kind: "text"; label: string; help?: string };

/**
 * Sentinel `<option>` value that swaps an enum select for a free-text box.
 * Deliberately starts with NUL: a real game config value never can, so it
 * cannot collide with a value the server reports and become a duplicate.
 */
export const CUSTOM_CHOICE = "\u0000custom";

const UNKNOWN_HELP =
  "Not in this app's catalogue — the control was chosen from the value the server reported.";

function numberedChoices(
  from: number,
  to: number,
  label: (n: number) => string
): SelectChoice[] {
  const out: SelectChoice[] = [];
  for (let n = from; n <= to; n += 1) out.push({ value: String(n), label: label(n) });
  return out;
}

/**
 * `GetServerOptions` / `ApplyServerOptions` — values are always strings.
 *
 * The set below matches what a 1.x server actually reports, which is not quite
 * what the community docs list: the API exposes `FG.EnableSeasonalEvents`
 * (positive), while `FG.DisableSeasonalEvents` is the client ini spelling. Both
 * are kept so either build gets a proper toggle; a key the server does not
 * report is never rendered, so the spare entry costs nothing.
 */
export const SERVER_OPTION_FIELDS: Record<string, FieldSpec> = {
  "FG.DSAutoPause": {
    kind: "bool",
    label: "Pause when empty",
    help: "Freezes the factory while nobody is connected.",
  },
  "FG.DSAutoSaveOnDisconnect": {
    kind: "bool",
    label: "Auto-save on disconnect",
    help: "Writes a save whenever a player leaves.",
  },
  "FG.AutosaveInterval": {
    kind: "number",
    label: "Autosave interval",
    min: 30,
    step: 30,
    unit: "seconds",
    help: "Seconds between automatic saves.",
  },
  "FG.ServerRestartTimeSlot": {
    kind: "number",
    label: "Restart time slot",
    step: 1,
    unit: "minutes",
    help: "Restart schedule in minutes. Servers ship with 1440 — once every 24 h.",
  },
  "FG.NetworkQuality": {
    kind: "enum",
    label: "Network quality",
    choices: [
      { value: "0", label: "0 — Low" },
      { value: "1", label: "1 — Medium" },
      { value: "2", label: "2 — High" },
      { value: "3", label: "3 — Ultra" },
    ],
    help: "Dedicated servers default to Low; connected clients should match this.",
  },
  "FG.WeatherPreset": {
    kind: "number",
    label: "Weather preset",
    step: 1,
    help: "Preset index. The values are undocumented; servers default to 0.",
  },
  "FG.SendGameplayData": {
    kind: "bool",
    label: "Send gameplay data",
    help: "Anonymous telemetry sent to Coffee Stain.",
  },
  "FG.AgreeToCrashUpload": {
    kind: "bool",
    label: "Upload crash reports",
  },
  "FG.EnableSeasonalEvents": {
    kind: "bool",
    label: "Seasonal events",
    help: "FICSMAS and friends. Enabled means the event content is active.",
  },
  "FG.DisableSeasonalEvents": {
    kind: "bool",
    label: "Disable seasonal events",
    help: "The inverted, client-ini spelling of the setting above.",
  },
};

/**
 * `GetAdvancedGameSettings` / `ApplyAdvancedGameSettings`.
 *
 * Every key here was read back off a running 1.x server, which reports all of
 * them as strings (`"False"`, `"2"`, `"Empty"`) even for the toggles — hence
 * the format-preserving writes.
 */
export const ADVANCED_SETTING_FIELDS: Record<string, FieldSpec> = {
  "FG.GameRules.NoPower": {
    kind: "bool",
    label: "No power",
    help: "Buildings run without electricity.",
  },
  "FG.GameRules.NoFuelCost": {
    kind: "bool",
    label: "No fuel cost",
    help: "Generators and vehicles burn nothing.",
  },
  "FG.GameRules.NoUnlockCost": {
    kind: "bool",
    label: "No unlock cost",
    help: "Milestones and MAM research cost nothing.",
  },
  "FG.GameRules.GiveAllTiers": {
    kind: "bool",
    label: "Unlock all tiers",
  },
  "FG.GameRules.UnlockInstantAltRecipes": {
    kind: "bool",
    label: "Instant alternate recipes",
    help: "Hard drive research completes immediately.",
  },
  "FG.GameRules.UnlockAllResearchSchematics": {
    kind: "bool",
    label: "Unlock all MAM research",
  },
  "FG.GameRules.UnlockAllResourceSinkSchematics": {
    kind: "bool",
    label: "Unlock the whole AWESOME shop",
  },
  "FG.GameRules.DisableArachnidCreatures": {
    kind: "bool",
    label: "Disable arachnid creatures",
  },
  "FG.GameRules.StartingTier": {
    kind: "enum",
    label: "Starting tier",
    choices: numberedChoices(0, 9, (n) => `Tier ${n}`),
    help: "Only takes effect when a new game is created.",
  },
  "FG.GameRules.SetGamePhase": {
    kind: "enum",
    label: "Game phase",
    choices: numberedChoices(0, 5, (n) => `Phase ${n}`),
    help: "1.x ships five phases; use a custom value if your build has more.",
  },
  "FG.GameRules.GiveItems": {
    kind: "text",
    label: "Give items",
    help: 'Item and amount to grant. Servers report "Empty" when nothing is set.',
  },
  "FG.PlayerRules.NoBuildCost": {
    kind: "bool",
    label: "No build cost",
    help: "Applies to the player who set it, not the whole server.",
  },
  "FG.PlayerRules.GodMode": {
    kind: "bool",
    label: "God mode",
    help: "Applies to the player who set it, not the whole server.",
  },
  "FG.PlayerRules.FlightMode": {
    kind: "bool",
    label: "Flight mode",
    help: "Applies to the player who set it, not the whole server.",
  },
};

/** `CreateNewGame` starting locations — the four spawn areas in 1.x. */
export const STARTING_LOCATIONS: SelectChoice[] = [
  { value: "", label: "Random (let the server pick)" },
  { value: "Grass Fields", label: "Grass Fields" },
  { value: "Rocky Desert", label: "Rocky Desert" },
  { value: "Northern Forest", label: "Northern Forest" },
  { value: "Dune Desert", label: "Dune Desert" },
];

const TRUTHY = new Set(["true", "1", "yes", "on", "enabled"]);

export function decodeBool(raw: unknown): boolean {
  if (typeof raw === "boolean") return raw;
  if (typeof raw === "number") return raw !== 0;
  return TRUTHY.has(String(raw ?? "").trim().toLowerCase());
}

/**
 * Whether a value reads as a boolean on its own.
 *
 * `0` / `1` deliberately do not count: they are indistinguishable from a real
 * small enum such as `FG.NetworkQuality`, and mislabelling one as a toggle
 * would silently clamp it to two of its four values.
 */
function looksBoolean(raw: unknown): boolean {
  if (typeof raw === "boolean") return true;
  const text = String(raw ?? "").trim().toLowerCase();
  return text === "true" || text === "false";
}

function looksNumeric(raw: unknown): boolean {
  if (typeof raw === "number") return Number.isFinite(raw);
  const text = String(raw ?? "").trim();
  return text !== "" && Number.isFinite(Number(text));
}

/**
 * Turn `FG.GameRules.NoBuildCost` into `No build cost` — sentence case, so a
 * key the catalogue does not know still reads like the labels next to it.
 * All-caps words are left alone (`FG.DSAutoPause` → `DS auto pause`).
 */
export function prettifyKey(key: string): string {
  const leaf = key.split(".").pop() || key;
  const words = leaf
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/_/g, " ")
    .split(/\s+/)
    .filter(Boolean);
  return words
    .map((word, index) => {
      if (word.length > 1 && word === word.toUpperCase()) return word;
      const lower = word.toLowerCase();
      return index === 0 ? lower.charAt(0).toUpperCase() + lower.slice(1) : lower;
    })
    .join(" ");
}

export function inferSpec(key: string, raw: unknown): FieldSpec {
  const label = prettifyKey(key);
  if (looksBoolean(raw)) return { kind: "bool", label, help: UNKNOWN_HELP };
  if (looksNumeric(raw)) return { kind: "number", label, help: UNKNOWN_HELP };
  return { kind: "text", label, help: UNKNOWN_HELP };
}

export function specFor(
  catalogue: Record<string, FieldSpec>,
  key: string,
  raw: unknown
): FieldSpec {
  return catalogue[key] ?? inferSpec(key, raw);
}

/** Catalogue order first (it is grouped by meaning), then anything else A–Z. */
export function orderKeys(
  catalogue: Record<string, FieldSpec>,
  keys: Iterable<string>
): string[] {
  const order = Object.keys(catalogue);
  return [...keys].sort((a, b) => {
    const ia = order.indexOf(a);
    const ib = order.indexOf(b);
    if (ia !== -1 && ib !== -1) return ia - ib;
    if (ia !== -1) return -1;
    if (ib !== -1) return 1;
    return a.localeCompare(b);
  });
}

/** Canonical editing form: booleans become `"true"`/`"false"`, everything else its text. */
export function toDraft(raw: unknown, spec: FieldSpec): string {
  if (spec.kind === "bool") return decodeBool(raw) ? "true" : "false";
  if (raw === null || raw === undefined) return "";
  return String(raw);
}

/** Render a boolean the same way the server reported this particular key. */
function boolLike(raw: unknown, value: boolean): string {
  const text = String(raw ?? "").trim();
  if (text === "1" || text === "0") return value ? "1" : "0";
  if (text === "true" || text === "false") return value ? "true" : "false";
  if (text === "TRUE" || text === "FALSE") return value ? "TRUE" : "FALSE";
  return value ? "True" : "False"; // what the FG.DS* options come back as
}

export function encodeOption(draft: string, spec: FieldSpec, raw: string): string {
  if (spec.kind === "bool") return boolLike(raw, draft === "true");
  return draft.trim();
}

/** How a server encodes advanced settings, for keys it did not report itself. */
export type ValueShape = "string" | "json";

/**
 * Which shape to assume for a key the server never sent back.
 *
 * A 1.x server reports every advanced setting as a string (`"False"`, `"2"`),
 * so writing a JSON `true` for a key it happened to omit would not match its
 * siblings. Read the shape off whatever it did report, and default to strings
 * when it reported nothing at all.
 */
export function shapeOf(current: Record<string, unknown>): ValueShape {
  const values = Object.values(current);
  if (values.length === 0) return "string";
  return values.some((value) => typeof value === "string") ? "string" : "json";
}

export function encodeAdvanced(
  draft: string,
  spec: FieldSpec,
  raw: unknown,
  fallback: ValueShape = "string"
): unknown {
  if (spec.kind === "bool") {
    const value = draft === "true";
    if (raw === undefined) return fallback === "json" ? value : boolLike("True", value);
    if (typeof raw === "boolean") return value;
    if (typeof raw === "number") return value ? 1 : 0;
    return boolLike(raw, value);
  }
  const text = draft.trim();
  if (spec.kind === "text") return text;
  if (typeof raw === "string") return text; // the server uses strings for this key
  if (raw === undefined && fallback === "string") return text;
  const num = Number(text);
  return text !== "" && Number.isFinite(num) ? num : text;
}
