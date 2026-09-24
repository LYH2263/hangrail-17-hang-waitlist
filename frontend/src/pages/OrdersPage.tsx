import { useEffect, useState } from "react";
import { api } from "../api/client";
type O = { id: number; ticket_code: string; garment_name: string; length_cm: number; status: string; due_at: string };
type Q = { id: number; order_id: number; ticket_code: string; garment_name: string; length_cm: number; enqueued_at: string };
type DrainResult = { hung: O[]; remaining: Q[] };
export default function OrdersPage() {
  const [rows, setRows] = useState<O[]>([]);
  const [queue, setQueue] = useState<Q[]>([]);
  const [msg, setMsg] = useState(""); const [err, setErr] = useState("");
  const reload = () => {
    api<O[]>("/orders").then(setRows);
    api<Q[]>("/queue").then(setQueue);
  };
  useEffect(() => { reload(); }, []);
  async function hang(id: number) {
    setMsg(""); setErr("");
    try {
      const o = await api<O>("/hang", { method: "POST", body: JSON.stringify({ order_id: id }) });
      setMsg(o.status === "queued" ? `${o.ticket_code} 空间不足,已入候挂队列` : `${o.ticket_code} 已上杆`);
      reload();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }
  async function drain() {
    setMsg(""); setErr("");
    try {
      const r = await api<DrainResult>("/queue/drain", { method: "POST", body: "{}" });
      setMsg(r.hung.length
        ? `消化 ${r.hung.length} 单:${r.hung.map(o => o.ticket_code).join("、")} 已上杆`
        : "队头仍放不下,队列保持不动");
      reload();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }
  return (<>
    <h2>工单</h2>
    {msg && <div className="ok">{msg}</div>}
    {err && <div className="err">{err}</div>}
    <table className="table"><thead><tr><th>票号</th><th>衣物</th><th>衣长</th><th>状态</th><th>到期</th><th></th></tr></thead>
    <tbody>{rows.map(o => <tr key={o.id}><td className="mono">{o.ticket_code}</td><td>{o.garment_name}</td><td className="mono">{o.length_cm}cm</td><td>{o.status === "queued" ? "候挂中" : o.status}</td>
      <td className="mono">{new Date(o.due_at).toLocaleString()}</td>
      <td>{(o.status === "ready" || o.status === "overdue") && <button onClick={() => hang(o.id)}>上杆</button>}</td>
    </tr>)}</tbody></table>

    <h3>候挂队列 {queue.length > 0 && <span className="mono">({queue.length})</span>}</h3>
    <div className="toolbar">
      <button onClick={drain} disabled={!queue.length}>按序消化</button>
    </div>
    <table className="table"><thead><tr><th>#</th><th>票号</th><th>衣物</th><th>衣长</th><th>入队时间</th></tr></thead>
    <tbody>{queue.map((q, i) => <tr key={q.id}>
      <td className="mono">{i === 0 ? "队头" : i + 1}</td>
      <td className="mono">{q.ticket_code}</td><td>{q.garment_name}</td>
      <td className="mono">{q.length_cm}cm</td>
      <td className="mono">{new Date(q.enqueued_at).toLocaleString()}</td>
    </tr>)}
      {!queue.length && <tr><td colSpan={5}>暂无候挂工单</td></tr>}
    </tbody></table>
  </>);
}
