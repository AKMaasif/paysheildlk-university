"use client";

import { CircleMarker, MapContainer, Popup, TileLayer } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import { markerTone, type MapCustomer, type MapEvent } from "@/lib/markers";

export type { MapCustomer, MapEvent };

const COLOR: Record<string, string> = {
  held: "#b86e00",
  blocked: "#b42318",
  watch: "#1f8a84",
  normal: "#98a29f",
};

export function SriLankaMap({
  customers,
  events,
  showCustomers,
  showEvents,
  onSelect,
}: {
  customers: MapCustomer[];
  events: MapEvent[];
  showCustomers: boolean;
  showEvents: boolean;
  onSelect: (title: string, sections: { label: string; value: string }[]) => void;
}) {
  return (
    <MapContainer center={[7.6, 80.7]} zoom={7} style={{ height: "100%", width: "100%" }}>
      <TileLayer attribution='&copy; OpenStreetMap contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
      {showCustomers && customers.filter((c) => c.lat != null && c.lng != null).map((customer) => (
        <CircleMarker
          key={customer.id}
          center={[customer.lat, customer.lng]}
          radius={customer.open_cases ? 9 : 6}
          pathOptions={{ color: COLOR[markerTone(customer)], fillColor: COLOR[markerTone(customer)], fillOpacity: 0.9, weight: 1 }}
          eventHandlers={{
            click: () => onSelect(customer.name, [
              { label: "Bank", value: customer.bank },
              { label: "District", value: `${customer.district}, ${customer.province}` },
              { label: "Score", value: String(customer.risk_score) },
              { label: "Open cases", value: String(customer.open_cases) },
              { label: "Held", value: String(customer.held_lkr) },
            ]),
          }}
        >
          <Popup>{customer.name}<br />{customer.district}</Popup>
        </CircleMarker>
      ))}
      {showEvents && events.filter((e) => e.lat != null && e.lng != null).map((event) => (
        <CircleMarker
          key={event.id}
          center={[event.lat + 0.05, event.lng + 0.05]}
          radius={5}
          pathOptions={{ color: "#5c5c5c", fillColor: "#ffffff", fillOpacity: 1, weight: 2 }}
          eventHandlers={{
            click: () => onSelect(event.event_type, [
              { label: "Customer", value: event.customer_name },
              { label: "District", value: event.district },
              { label: "When", value: event.occurred_at },
            ]),
          }}
        />
      ))}
    </MapContainer>
  );
}
