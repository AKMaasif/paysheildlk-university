// What the system actually knows about a message or a SIM freeze, worded without claiming more.
// No imports, so it can be tested on its own.

export type Channel = "sms" | "email" | "push";

type Delivery = {
  state?: string;
  confirmed?: boolean;
  provider?: string | null;
  provider_message_id?: string | null;
  reason?: string | null;
  retryable?: boolean;
};

type Result = { delivery?: Delivery; attempts?: number; status?: string; [key: string]: unknown } | null | undefined;

const REASON: Record<string, string> = {
  timeout: "timed out",
  unreachable: "provider unreachable",
  rejected_credentials: "credentials refused",
  rate_limited: "rate limited",
  provider_error: "provider error",
  rejected: "rejected",
  no_address: "no email address on file",
  no_phone: "no mobile number on file",
  no_device_token: "no push token",
  auth_failed: "push sign-in refused",
  recipient_refused: "recipient refused",
  request_error: "request error",
};

export function deliveryLine(channel: Channel, result: Result): { text: string; failed: boolean } {
  const d = result?.delivery;
  if (!d || !d.state) {
    // An event written before delivery records existed, or one that carries no result. Say so rather than guess.
    return { text: `${channel}=status not recorded`, failed: false };
  }
  const tries = result?.attempts && result.attempts > 1 ? ` after ${result.attempts} attempts` : "";
  switch (d.state) {
    case "logged_only":
      return { text: `${channel}=LOGGED ONLY, nothing was sent`, failed: false };
    case "inbox_stored":
      return { text: `${channel}=INBOX ONLY, no provider was asked`, failed: false };
    case "blocked_not_allowlisted":
      return { text: `${channel}=NOT SENT, recipient is not on the allow-list`, failed: false };
    case "blocked_budget":
      return { text: `${channel}=NOT SENT, hourly limit on real messages reached`, failed: false };
    case "provider_accepted": {
      const id = d.provider_message_id ? ` id=${d.provider_message_id}` : "";
      return { text: `${channel}=ACCEPTED by ${d.provider || "provider"}${id}, delivery UNCONFIRMED`, failed: false };
    }
    case "delivered":
      return { text: `${channel}=DELIVERED, confirmed by ${d.provider || "provider"}`, failed: false };
    case "failed": {
      const why = REASON[d.reason || ""] || d.reason || "unknown reason";
      return { text: `${channel}=FAILED (${why})${tries}`, failed: true };
    }
    default:
      return { text: `${channel}=${d.state}`, failed: false };
  }
}

export function freezeLine(result: Result): string {
  if (result && (result as { simulated?: boolean }).simulated === false && (result as { operator_contacted?: boolean }).operator_contacted) {
    return "SIM freeze REQUESTED from the operator";
  }
  return "SIM freeze request RECORDED in PayShield only (simulated), operator not contacted";
}
