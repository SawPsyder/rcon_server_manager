import { useMemo } from "react";

type Props = {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  /** Optional hint under the control (e.g. timezone name). */
  hint?: string;
  id?: string;
};

const HOURS = Array.from({ length: 24 }, (_, i) => i);
const MINUTES = Array.from({ length: 60 }, (_, i) => i);

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

function parseTime(value: string): { hour: number; minute: number } {
  const m = /^([01]?\d|2[0-3]):([0-5]\d)$/.exec((value || "").trim());
  if (!m) return { hour: 4, minute: 0 };
  return { hour: Number(m[1]), minute: Number(m[2]) };
}

function formatTime(hour: number, minute: number): string {
  return `${pad2(hour)}:${pad2(minute)}`;
}

export default function TimePicker({ value, onChange, disabled, hint, id }: Props) {
  const { hour, minute } = useMemo(() => parseTime(value), [value]);

  const setHour = (h: number) => onChange(formatTime(h, minute));
  const setMinute = (m: number) => onChange(formatTime(hour, m));

  return (
    <div className={`time-picker${disabled ? " is-disabled" : ""}`} id={id}>
      <div className="time-picker-shell" role="group" aria-label="Time">
        <select
          className="time-picker-part"
          aria-label="Hour"
          disabled={disabled}
          value={hour}
          onChange={(e) => setHour(Number(e.target.value))}
        >
          {HOURS.map((h) => (
            <option key={h} value={h}>
              {pad2(h)}
            </option>
          ))}
        </select>
        <span className="time-picker-sep" aria-hidden="true">
          :
        </span>
        <select
          className="time-picker-part"
          aria-label="Minute"
          disabled={disabled}
          value={minute}
          onChange={(e) => setMinute(Number(e.target.value))}
        >
          {MINUTES.map((m) => (
            <option key={m} value={m}>
              {pad2(m)}
            </option>
          ))}
        </select>
      </div>
      {hint ? <div className="time-picker-hint">{hint}</div> : null}
    </div>
  );
}
