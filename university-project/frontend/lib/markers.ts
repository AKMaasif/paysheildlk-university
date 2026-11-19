export type MapCustomer = {
  id: string;
  name: string;
  bank: string;
  province: string;
  district: string;
  lat: number;
  lng: number;
  risk_score: number;
  severity: string;
  open_cases: number;
  held_lkr: number;
};

export type MapEvent = {
  id: string;
  event_type: string;
  lat: number;
  lng: number;
  district: string;
  occurred_at: string;
  customer_name: string;
};

export function markerTone(customer: MapCustomer): "held" | "blocked" | "watch" | "normal" {
  if (customer.held_lkr > 0) return "held";
  if (customer.severity === "high" && customer.open_cases > 0) return "blocked";
  if (customer.open_cases > 0 || customer.severity === "medium") return "watch";
  return "normal";
}
