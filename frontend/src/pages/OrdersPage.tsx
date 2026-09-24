import { useEffect, useState } from "react";
import { api } from "../api/client";
type O = { id: number; ticket_code: string; garment_name: string; length_cm: number; status: string; due_at: string };
type Q = { id: number; order_id: number; ticket_code: string; garment_name: string; length_cm: number; enqueued_at: string };
type DrainOut = { hung: O[]; blocked_order_id: number | null; remaining: number };
const STATUS_CN: Record<string, string> = {
  ready: "待上杆",
  waiting: "候挂中",
  hung: "在杆",
  picked: "已取走",
  overdue: "已逾期",
};
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
      setMsg(o.status === "waiting" ? `${o.ticket_code} 空间不足，已入候挂队列` : `${o.ticket_code} 已上杆`);
      reload();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }
  async function drain() {
    setMsg(""); setErr("");
    try {
      const r = await api<DrainOut>("/queue/drain", { method: "POST" });
      if (r.hung.length > 0) {
        const codes = r.hung.map((o) => o.ticket_code).join("、");
        setMsg(r.remaining > 0 ? `已上杆 ${codes}；队头空间不足，仍余 ${r.remaining} 单候挂` : `已上杆 ${codes}，候挂队列已清空`);
      } else {
        setMsg(r.remaining > 0 ? `队头工单空间不足，${r.remaining} 单仍在候挂` : "候挂队列为空");
      }
      reload();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }
  return (<>
    <h2>工单</h2>
    {msg && <div className="ok">{msg}</div>}
    {err && <div className="err">{err}</div>}
    <div className="queue-panel">
      <div className="queue-panel-head">
        <span>候挂队列（{queue.length}）</span>
        <button onClick={drain} disabled={queue.length === 0}>按序消化</button>
      </div>
      {queue.length === 0 && <div className="tray-empty">无候挂工单 — 上杆空间不足时会自动排队</div>}
      {queue.length > 0 && (
        <div className="tray-chips">
          {queue.map((q, i) => (
            <span key={q.id} className={`tray-chip${i === 0 ? " tray-chip--head" : ""}`}
              title={`${q.garment_name} ${q.length_cm}cm · ${new Date(q.enqueued_at).toLocaleString()} 入队`}>
              {i + 1}. {q.ticket_code}
            </span>
          ))}
        </div>
      )}
    </div>
    <table className="table"><thead><tr><th>票号</th><th>衣物</th><th>衣长</th><th>状态</th><th>到期</th><th></th></tr></thead>
    <tbody>{rows.map(o => <tr key={o.id}><td className="mono">{o.ticket_code}</td><td>{o.garment_name}</td><td className="mono">{o.length_cm}cm</td><td>{STATUS_CN[o.status] ?? o.status}</td>
      <td className="mono">{new Date(o.due_at).toLocaleString()}</td>
      <td>{(o.status === "ready" || o.status === "overdue" || o.status === "waiting") && <button onClick={() => hang(o.id)}>上杆</button>}</td>
    </tr>)}</tbody></table>
  </>);
}
