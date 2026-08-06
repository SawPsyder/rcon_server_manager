type Props = {
  hasInfo: boolean;
  disabled?: boolean;
  title?: string;
  onClick: () => void;
};

/** Small "i" control — grey when empty, blue when history/notes exist. */
export default function IdentityInfoButton({
  hasInfo,
  disabled,
  title,
  onClick,
}: Props) {
  return (
    <button
      type="button"
      className={`info-btn ${hasInfo ? "has-info" : "no-info"}`}
      disabled={disabled}
      title={title || (hasInfo ? "View player history & notes" : "Player info (no records yet)")}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
    >
      i
    </button>
  );
}
