"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { isSignedIn } from "@/lib/api";

export default function Home() {
  const router = useRouter();
  useEffect(() => {
    router.replace(isSignedIn("operator") ? "/overview" : "/signin");
  }, [router]);
  return <div className="signin-page">Opening PayShield…</div>;
}
