"use client";

import { useEffect, useState } from "react";
import { Note, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";

type Question = { code: string; prompt: string };

export default function EnrolPage() {
  const [bank, setBank] = useState<Question[]>([]);
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [form, setForm] = useState({ full_name: "", phone: "", email: "", nic: "", account_number: "", district: "Colombo", branch: "", balance_lkr: "0" });
  const [added, setAdded] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api<{ questions: Question[] }>("/api/questions").then((body) => setBank(body.questions)).catch((err) => setError(err.message));
  }, []);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setAdded("");
    const answers = Object.entries(picked).filter(([, value]) => value.trim()).map(([code, answer]) => ({ code, answer }));
    try {
      await api("/api/enrol", {
        method: "POST",
        body: JSON.stringify({ ...form, balance_lkr: Number(form.balance_lkr || 0), answers }),
      });
      setAdded(`${form.full_name} is on the BankCylone record. They can now register at /c/register with these exact details.`);
      setForm({ full_name: "", phone: "", email: "", nic: "", account_number: "", district: "Colombo", branch: "", balance_lkr: "0" });
      setPicked({});
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the customer");
    }
  }

  const field = (key: keyof typeof form) => (event: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [key]: event.target.value });

  return (
    <>
      <PageHeader crumb="Assure / Add customer" title="Add customer" subtitle="This is the bank's record. The customer claims it by registering in the app, where every detail must match." chip="BANKCYLONE" />
      {error && <Note tone="warn">{error}</Note>}
      {added && <Note>{added}</Note>}
      <form className="card" onSubmit={save}>
        <div className="split">
          <label className="field"><span>Full name</span><input value={form.full_name} onChange={field("full_name")} required /></label>
          <label className="field"><span>Email</span><input value={form.email} onChange={field("email")} required /></label>
          <label className="field"><span>NIC number</span><input value={form.nic} onChange={field("nic")} required /></label>
          <label className="field"><span>Mobile number</span><input value={form.phone} onChange={field("phone")} placeholder="+94771234567" required /></label>
          <label className="field"><span>Bank account number</span><input value={form.account_number} onChange={field("account_number")} required /></label>
          <label className="field"><span>Opening balance (Rs)</span><input value={form.balance_lkr} onChange={field("balance_lkr")} inputMode="numeric" required /></label>
          <label className="field"><span>District</span><input value={form.district} onChange={field("district")} required /></label>
          <label className="field"><span>Branch</span><input value={form.branch} onChange={field("branch")} /></label>
        </div>
        <p className="quiet">Bank: BankCylone. There is no password here. The customer chooses one when they register.</p>
        <h2>Verification questions</h2>
        <p className="quiet">Pick at least six. The customer will be asked three at a time, in a random order. Answers are stored as salted hashes.</p>
        {bank.map((item) => (
          <label className="field" key={item.code}>
            <span>{item.prompt}</span>
            <input value={picked[item.code] || ""} onChange={(event) => setPicked({ ...picked, [item.code]: event.target.value })} />
          </label>
        ))}
        <button className="btn primary" type="submit">Add to the BankCylone record</button>
      </form>
    </>
  );
}
