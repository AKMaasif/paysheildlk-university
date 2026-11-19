import { useId } from "react";
export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.6,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  const paths: Record<string, JSX.Element> = {
    logo: (
      <>
        <path d="M4 16.5 9 6l4 7 2-3.5L20 16.5" />
        <path d="M4 19h16" />
      </>
    ),
    refresh: (
      <>
        <path d="M20 12a8 8 0 1 1-2.2-5.5" />
        <path d="M20 4v5h-5" />
      </>
    ),
    snapshot: (
      <>
        <rect x="4" y="5" width="16" height="14" rx="1" />
        <path d="M8 15l2.5-3 2 2.2L16 10l2 5" />
      </>
    ),
    cases: (
      <>
        <path d="M5 7h14v12H5z" />
        <path d="M9 7V5h6v2" />
        <path d="M8 12h8M8 15h5" />
      </>
    ),
    hold: (
      <>
        <rect x="4" y="6" width="16" height="12" rx="1" />
        <path d="M8 12h8" />
      </>
    ),
    map: (
      <>
        <path d="M9 4.5 4 6.5v13l5-2 6 2 5-2v-13l-5 2-6-2z" />
        <path d="M9 4.5v13M15 6.5v13" />
      </>
    ),
    search: (
      <>
        <circle cx="11" cy="11" r="6" />
        <path d="m16 16 4 4" />
      </>
    ),
    filter: (
      <>
        <path d="M4 6h16M7 12h10M10 18h4" />
      </>
    ),
    live: (
      <>
        <circle cx="6" cy="7" r="2" />
        <circle cx="18" cy="7" r="2" />
        <circle cx="12" cy="17" r="2" />
        <path d="M8 8.5 10.5 15M16 8.5 13.5 15" />
      </>
    ),
    model: (
      <>
        <rect x="4" y="4" width="6" height="6" />
        <rect x="14" y="4" width="6" height="6" />
        <rect x="9" y="14" width="6" height="6" />
      </>
    ),
    export: (
      <>
        <path d="M12 4v10" />
        <path d="m8 10 4 4 4-4" />
        <path d="M5 19h14" />
      </>
    ),
    demo: (
      <>
        <path d="M8 6h8v12H8z" />
        <path d="M10 9h4M10 12h4M10 15h2" />
      </>
    ),
    overview: (
      <>
        <rect x="4" y="4" width="7" height="7" />
        <rect x="13" y="4" width="7" height="7" />
        <rect x="4" y="13" width="7" height="7" />
        <rect x="13" y="13" width="7" height="7" />
      </>
    ),
    sim: (
      <>
        <rect x="7" y="3" width="10" height="18" rx="1" />
        <path d="M10 8h4M10 12h4M10 16h4" />
      </>
    ),
    chart: (
      <>
        <path d="M4 19h16" />
        <path d="M6 16l4-5 3 3 5-7" />
      </>
    ),
    report: (
      <>
        <path d="M7 3h7l5 5v13H7z" />
        <path d="M14 3v5h5M9 13h6M9 17h6" />
      </>
    ),
    admin: (
      <>
        <circle cx="12" cy="8" r="3" />
        <path d="M5 19c1.5-3 3.8-4.5 7-4.5S17.5 16 19 19" />
      </>
    ),
    settings: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4" />
      </>
    ),
    select: (
      <>
        <path d="M5 6h10M5 12h10M5 18h6" />
        <path d="m16 15 3 3 3-6" />
      </>
    ),
  };
  return <svg {...common}>{paths[name] || paths.overview}</svg>;
}

const SHIELD = "M30 8 H112 V62 C112 90 92 112 64 122 C36 112 16 90 16 62 V22 Z";

/** The PayShield.lk mark: a shield with a SIM-card corner, chip contacts and a check. */
export function Logo({ size = 28, onDark = false }: { size?: number; onDark?: boolean }) {
  const id = useId().replace(/:/g, "");
  const [top, bottom] = onDark ? ["#1fa397", "#0f6b6b"] : ["#0f6b6b", "#093f45"];
  return (
    <svg width={Math.round((size * 128) / 130)} height={size} viewBox="0 0 128 130" role="img" aria-label="PayShield.lk" style={{ flex: "none" }}>
      <defs>
        <linearGradient id={id} x1="16" y1="8" x2="112" y2="122" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor={top} />
          <stop offset="1" stopColor={bottom} />
        </linearGradient>
      </defs>
      <path d={SHIELD} fill={`url(#${id})`} />
      <g fill="none" stroke="#ffffff" strokeOpacity={onDark ? 0.3 : 0.22} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
        <rect x="38" y="36" width="52" height="48" rx="9" />
        <path d="M38 52H90M38 68H90M64 36V52M64 68V84" />
      </g>
      <path d="M45 64 L58 77 L84 47" fill="none" stroke="#6ff2d6" strokeWidth="10" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
