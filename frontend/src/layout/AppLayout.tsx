import { useState } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import {
  LayoutDashboard, Siren, Map, Truck, Tent, Route, ClipboardList,
  ShieldCheck, ListChecks, Bell, FileText, FlaskConical, X,
} from "lucide-react";
import { getActor, setActor } from "../api";
import { useLiveUpdates } from "../api/live";
import { SEVERITY_STYLE } from "../lib/format";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/incidents", label: "Incident Center", icon: Siren },
  { to: "/map", label: "Live Map", icon: Map },
  { to: "/resources", label: "Resource Center", icon: Truck },
  { to: "/shelters", label: "Shelters", icon: Tent },
  { to: "/routes", label: "Route & Access", icon: Route },
  { to: "/planning", label: "Response Planning", icon: ClipboardList },
  { to: "/approvals", label: "Approval Center", icon: ShieldCheck },
  { to: "/actions", label: "Action Tracker", icon: ListChecks },
  { to: "/alerts", label: "Alert Center", icon: Bell },
  { to: "/reports", label: "Situation Reports", icon: FileText },
  { to: "/simulation", label: "Simulation", icon: FlaskConical },
];

const STATUS_STYLE = {
  live: { dot: "bg-emerald-400", label: "Live" },
  connecting: { dot: "bg-amber-400 animate-pulse", label: "Connecting" },
  offline: { dot: "bg-red-500", label: "Offline: retrying" },
  mock: { dot: "bg-slate-500", label: "Mock data" },
} as const;

export default function AppLayout() {
  const { status, toasts, dismiss } = useLiveUpdates();
  const [actor, setActorState] = useState(getActor);
  const st = STATUS_STYLE[status];

  return (
    <div className="flex h-screen bg-slate-950 text-slate-100">
      <aside className="flex w-60 shrink-0 flex-col border-r border-slate-800 bg-slate-900">
        <div className="border-b border-slate-800 px-4 py-5">
          <p className="text-xs uppercase tracking-wider text-slate-400">Emergency Ops</p>
          <h1 className="text-lg font-semibold">Disaster Response</h1>
        </div>
        <nav className="flex-1 space-y-1 overflow-y-auto p-2">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-md px-3 py-2 text-sm transition ${
                  isActive ? "bg-red-600/20 text-red-300" : "text-slate-300 hover:bg-slate-800"
                }`
              }
            >
              <Icon size={18} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="space-y-2 border-t border-slate-800 p-3 text-xs">
          <p className="flex items-center gap-2 text-slate-400">
            <span className={`h-2 w-2 rounded-full ${st.dot}`} /> {st.label}
          </p>
          <label className="block text-slate-500">
            Acting as (recorded in audit trail)
            <input
              className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200"
              value={actor}
              onChange={(e) => setActorState(e.target.value)}
              onBlur={() => setActor(actor)}
            />
          </label>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="border-b border-amber-700/40 bg-amber-950/40 px-6 py-2 text-xs text-amber-200">
          Decision-support prototype. AI recommendations are advisory and require approval
          by authorized emergency personnel.
        </header>
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>

      {/* Alert toasts pushed over the WebSocket */}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[1000] w-96 space-y-2">
        {toasts.map(({ id, alert }) => (
          <div key={id} className={`pointer-events-auto rounded-md border-l-4 bg-slate-900 p-3 text-sm shadow-lg ring-1 ring-slate-700 ${SEVERITY_STYLE[alert.severity].border}`}>
            <div className="flex items-start justify-between gap-2">
              <p className={`text-xs font-semibold uppercase ${SEVERITY_STYLE[alert.severity].text}`}>
                {SEVERITY_STYLE[alert.severity].label} · {alert.kind.replace(/_/g, " ")}
              </p>
              <button onClick={() => dismiss(id)} className="text-slate-500 hover:text-slate-200" aria-label="Dismiss"><X size={14} /></button>
            </div>
            <p className="mt-1">{alert.message}</p>
            <Link to="/alerts" onClick={() => dismiss(id)} className="mt-1 inline-block text-xs text-sky-400 hover:underline">Alert Center</Link>
          </div>
        ))}
      </div>
    </div>
  );
}
