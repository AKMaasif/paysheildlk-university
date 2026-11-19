"use client";

import { useEffect } from "react";
import { FraudAnalytics } from "@/components/FraudAnalytics";
import { PageHeader } from "@/components/ui";
import { useShell } from "@/components/Shell";

export default function AnalyticsPage() {
  const { setDetails } = useShell();
  useEffect(() => {
    setDetails(null);
  }, [setDetails]);
  return (
    <>
      <PageHeader crumb="Monitor / Fraud analytics" title="Fraud analytics" subtitle="Payments by day, province and district · Asia/Colombo" chip="SIMULATED" />
      <FraudAnalytics full />
    </>
  );
}
