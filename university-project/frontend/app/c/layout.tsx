"use client";

import { useEffect } from "react";
import "./site.css";

export default function CustomerSiteLayout({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => undefined);
    }
  }, []);
  return <div className="cs">{children}</div>;
}
