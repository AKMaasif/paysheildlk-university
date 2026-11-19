import { Brand, Ico } from "@/components/CustomerChrome";

export function AuthSide({ title, text }: { title: string; text: string }) {
  return (
    <aside className="cs-auth-side">
      <Brand onDark size={34} />
      <div>
        <h2>{title}</h2>
        <p>{text}</p>
        <ul>
          <li><Ico name="phone" size={20} /> The question about a payment appears on the phone you registered.</li>
          <li><Ico name="lock" size={20} /> We never ask for a code or a password by SMS, email or phone.</li>
          <li><Ico name="receipt" size={20} /> Every alert ends with a receipt that lists each step and its time.</li>
        </ul>
      </div>
      <small>Pilot only. Simulated network, fictional customers. Not connected to a live bank or operator.</small>
    </aside>
  );
}
