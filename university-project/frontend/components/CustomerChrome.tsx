"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Logo } from "@/components/Icons";
import { clearToken } from "@/lib/api";

type IconName = "check" | "lock" | "phone" | "camera" | "receipt" | "wallet" | "key" | "bell" | "clock" | "eye";

const PATHS: Record<IconName, JSX.Element> = {
  check: <path d="M5 12.5 10 17.5 19 7" />,
  lock: (
    <>
      <rect x="5" y="11" width="14" height="9" rx="2" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </>
  ),
  phone: (
    <>
      <rect x="7" y="3" width="10" height="18" rx="2.5" />
      <path d="M11 18h2" />
    </>
  ),
  camera: (
    <>
      <path d="M4 8h3l1.5-2h7L17 8h3v11H4z" />
      <circle cx="12" cy="13" r="3.2" />
    </>
  ),
  receipt: (
    <>
      <path d="M6 3h12v18l-3-2-3 2-3-2-3 2z" />
      <path d="M9 8h6M9 12h6" />
    </>
  ),
  wallet: (
    <>
      <path d="M4 7a2 2 0 0 1 2-2h11v4" />
      <rect x="4" y="7" width="16" height="12" rx="2" />
      <circle cx="16" cy="13" r="1" />
    </>
  ),
  key: (
    <>
      <circle cx="8" cy="14" r="3.5" />
      <path d="m10.5 11.5 8-8M15 7l2.5 2.5" />
    </>
  ),
  bell: (
    <>
      <path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z" />
      <path d="M10 20a2 2 0 0 0 4 0" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  eye: (
    <>
      <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" />
      <circle cx="12" cy="12" r="2.8" />
    </>
  ),
};

export function Ico({ name, size = 22 }: { name: IconName; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {PATHS[name]}
    </svg>
  );
}

export function Brand({ onDark = false, size = 32 }: { onDark?: boolean; size?: number }) {
  return (
    <Link href="/c" className="cs-brand" aria-label="PayShield.lk home">
      <Logo size={size} onDark={onDark} />
      <strong>
        PayShield<span>.lk</span>
      </strong>
    </Link>
  );
}

export function SiteNav({ signedIn }: { signedIn: boolean }) {
  return (
    <header className="cs-nav">
      <div className="cs-wrap">
        <Brand />
        <nav className="cs-links" aria-label="Sections">
          <a href="#what-is-a-sim-swap">The problem</a>
          <a href="#how-it-works">How it works</a>
          <a href="#what-you-get">Your account</a>
          <a href="#questions">Questions</a>
        </nav>
        <div className="cs-nav-actions">
          {signedIn ? (
            <Link className="cs-btn primary small" href="/c/home">Open my account</Link>
          ) : (
            <>
              <Link className="cs-btn ghost small" href="/c/login">Log in</Link>
              <Link className="cs-btn primary small" href="/c/register">Register</Link>
            </>
          )}
        </div>
      </div>
    </header>
  );
}

export function AppBar({ name }: { name?: string }) {
  const path = usePathname();
  const router = useRouter();
  function signOut() {
    clearToken("customer");
    localStorage.removeItem("payshield_customer_user");
    router.replace("/c");
  }
  return (
    <header className="cs-appbar">
      <div className="cs-wrap">
        <Brand onDark size={30} />
        <nav className="cs-appnav" aria-label="Account">
          <Link href="/c/home" className={path === "/c/home" ? "on" : ""}>My account</Link>
          <Link href="/c/password" className={path === "/c/password" ? "on" : ""}>Password</Link>
          {name && <span style={{ color: "#c4dcd8", fontSize: 13.5, padding: "0 8px" }}>{name.split(" ")[0]}</span>}
          <button onClick={signOut}>Sign out</button>
        </nav>
      </div>
    </header>
  );
}
