"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { DemoPanel } from "@/components/DemoPanel";
import { Journal } from "@/components/Journal";
import type { LiveApproval, LiveStatus } from "@/lib/live/status";

// Polls every 15 s, only while the tab is visible (COST.md "M8 dashboard").
const POLL_MS = 15_000;

export function LiveView({ login, investigation }: { login: string; investigation?: string }) {
  const [data, setData] = useState<LiveStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const query = investigation ? `?investigation=${encodeURIComponent(investigation)}` : "";

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`/api/live/status${query}`, { cache: "no-store" });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error ?? res.statusText);
      setData(body);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [query]);

  useEffect(() => {
    const first = setTimeout(refresh, 0);
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, POLL_MS);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [refresh]);

  async function decide(kind: "approve" | "reject", a: LiveApproval) {
    if (!data?.investigation) return;
    const res = await fetch(`/api/live/${kind}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        investigation_id: data.investigation.id,
        item: a.item,
        ...(kind === "approve" ? { action_hash: a.hash } : {}),
      }),
    });
    const body = await res.json().catch(() => ({}));
    setMessage(res.ok ? `${kind === "approve" ? "Sent to the Actor" : "Rejected"}: ${a.action}` : `Refused: ${body.error}`);
    setConfirming(null);
    refresh();
  }

  // Expiry is measured against the server's time of the last check, which
  // keeps rendering pure and moves the countdown on every poll.
  const nowSeconds = data ? Math.floor(Date.parse(data.checked_at) / 1000) : 0;
  return (
    <>
      <h1>Live</h1>
      <p className="small muted">
        Signed in as {login}.{" "}
        <Link href="/api/auth/signout" prefetch={false}>
          Sign out
        </Link>
        .{" "}
        {data ? `Checked ${data.checked_at.slice(11, 19)} UTC.` : "Loading."}
      </p>
      <DemoPanel status={data} nowSeconds={nowSeconds} />
      {error && <p className="badge badge-bad">Could not read the live state: {error}</p>}
      {message && <p className="card">{message}</p>}
      {data && (
        <>
          <h2>Alarms</h2>
          <div className="table-scroll">
            <table>
              <tbody>
                {data.alarms.map((a) => (
                  <tr key={a.name}>
                    <th scope="row"><code>{a.name}</code></th>
                    <td>
                      <span className={`outcome outcome-${a.state === "OK" ? "right" : a.state === "ALARM" ? "wrong" : "hedged"}`}>
                        {a.state}
                      </span>
                    </td>
                    <td className="small muted">{a.since}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="small">
            Last hour: {data.last_hour.checkouts_placed} checkouts placed,{" "}
            {data.last_hour.orders_paid} orders paid, {data.last_hour.lambda_errors} Lambda errors.
          </p>

          <h2>Investigation</h2>
          {!data.investigation ? (
            <p className="muted">No investigation is open.</p>
          ) : (
            <>
              <p>
                <code>{data.investigation.id}</code>, paged by{" "}
                <code>{data.investigation.trigger ?? "unknown"}</code>, {data.investigation.model ?? ""}.{" "}
                {data.investigation.finished ? `Finished (${data.investigation.stop_reason}).` : "Running."}
              </p>
              {data.report && (
                <p className="card">
                  <strong>
                    {data.report.component} / {data.report.category}
                  </strong>
                  , confidence {data.report.confidence}. {data.report.summary}
                </p>
              )}
              <h3>Approvals</h3>
              {data.approvals.length === 0 && <p className="muted">None proposed.</p>}
              {data.approvals.map((a) => {
                const left = a.expires_at - nowSeconds;
                const usable = a.status === "pending" && a.intact && left > 0;
                return (
                  <div key={a.item} className="card" style={{ marginBottom: 8 }}>
                    <code>{a.action}</code> <span className="badge">{a.status}</span>{" "}
                    {!a.intact && <span className="badge badge-bad">record edited</span>}
                    <span className="small muted">
                      {" "}
                      {left > 0 ? `expires in ${Math.floor(left / 60)}m${String(left % 60).padStart(2, "0")}s` : "expired"}
                      {a.decided_by ? `, by ${a.decided_by}` : ""}
                    </span>
                    {usable && (
                      <div style={{ marginTop: 8, display: "flex", gap: 8, flexWrap: "wrap" }}>
                        {confirming === a.item ? (
                          <>
                            <button type="button" onClick={() => decide("approve", a)}>
                              Run exactly this action
                            </button>
                            <button type="button" onClick={() => setConfirming(null)}>
                              Cancel
                            </button>
                          </>
                        ) : (
                          <button type="button" onClick={() => setConfirming(a.item)}>
                            Approve...
                          </button>
                        )}
                        <button type="button" onClick={() => decide("reject", a)}>
                          Reject
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
              <h3>Journal</h3>
              <Journal
                steps={data.investigation.steps.map((s) => ({ ...s, cited: false }))}
                injectedAt={data.investigation.started_at ?? data.checked_at}
              />
            </>
          )}
        </>
      )}
    </>
  );
}
