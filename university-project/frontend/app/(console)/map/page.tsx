"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { Note, PageHeader } from "@/components/ui";
import { useShell } from "@/components/Shell";
import { api } from "@/lib/api";
import { lkr } from "@/lib/format";
import type { MapCustomer, MapEvent } from "@/components/SriLankaMap";

const MapView = dynamic(() => import("@/components/SriLankaMap").then((mod) => mod.SriLankaMap), { ssr: false });

export default function MapPage() {
  const { refreshKey, setDetails } = useShell();
  const [customers, setCustomers] = useState<MapCustomer[]>([]);
  const [events, setEvents] = useState<MapEvent[]>([]);
  const [showCustomers, setShowCustomers] = useState(true);
  const [showEvents, setShowEvents] = useState(true);

  useEffect(() => {
    api<{ customers: MapCustomer[]; sim_events: MapEvent[] }>("/api/map")
      .then((body) => { setCustomers(body.customers); setEvents(body.sim_events); })
      .catch(() => { setCustomers([]); setEvents([]); });
  }, [refreshKey]);

  return (
    <>
      <PageHeader crumb="Monitor / Map" title="Sri Lanka map" subtitle={`${customers.length} customers · OpenStreetMap`} chip="LIVE" />
      <Note>Colour is the case state, not a coverage forecast. Large dots have an open case.</Note>
      <div className="map-frame">
        <div className="map-panel">
          <h2>Layers</h2>
          <label><input type="checkbox" checked={showCustomers} onChange={(event) => setShowCustomers(event.target.checked)} /> Customers</label>
          <label><input type="checkbox" checked={showEvents} onChange={(event) => setShowEvents(event.target.checked)} /> SIM events</label>
          <h2 style={{ marginTop: 12 }}>Legend</h2>
          <div><i className="legend-dot" style={{ background: "#98a29f" }} /> Normal</div>
          <div><i className="legend-dot" style={{ background: "#1f8a84" }} /> Watch</div>
          <div><i className="legend-dot" style={{ background: "#b86e00" }} /> Held</div>
          <div><i className="legend-dot" style={{ background: "#b42318" }} /> Blocked</div>
          <p className="fine">Open cases in view: {customers.reduce((sum, item) => sum + item.open_cases, 0)}. Held: {lkr(customers.reduce((sum, item) => sum + item.held_lkr, 0))}.</p>
        </div>
        <MapView
          customers={customers}
          events={events}
          showCustomers={showCustomers}
          showEvents={showEvents}
          onSelect={(title, sections) => setDetails({ title, sections: sections.map((section) => ({ label: section.label, value: section.label === "Held" ? lkr(Number(section.value)) : section.value })) })}
        />
      </div>
    </>
  );
}
