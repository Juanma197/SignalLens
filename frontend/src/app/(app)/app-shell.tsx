"use client";
import Link from "next/link";
import {usePathname, useRouter} from "next/navigation";
import {FormEvent, ReactNode, useState} from "react";

const ICON: Record<string, ReactNode> = {
  dashboard: <path d="M3 10.5 12 3l9 7.5V21h-6v-6H9v6H3z"/>,
  picks: <path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3 6.4 20.2l1.1-6.2L3 9.6l6.2-.9z"/>,
  portfolio: <path d="M3 8h18v12H3zM8 8V5h8v3M3 13h18"/>,
  watchlist: <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12zm10 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"/>,
  checks: <path d="M4 12.5 9 17.5 20 6.5"/>,
  history: <path d="M12 7v5l3 2M3.5 12a8.5 8.5 0 1 0 2.5-6L3.5 8.5M3.5 4v4.5H8"/>,
  snapshots: <path d="M4 7h3l2-2h6l2 2h3v12H4zM12 16.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z"/>,
  settings: <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm7.4-3a7.4 7.4 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7.5 7.5 0 0 0-2-1.2L14.5 3h-5l-.4 2.6a7.5 7.5 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7.4 7.4 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7.5 7.5 0 0 0 2 1.2l.4 2.6h5l.4-2.6a7.5 7.5 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2z"/>,
  search: <path d="M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zm9 3-4-4"/>,
};
export const Icon = ({name}: {name: keyof typeof ICON}) =>
  <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{ICON[name]}</svg>;

const LINKS: {href: string; label: string; icon: keyof typeof ICON; also?: string[]}[] = [
  {href: "/", label: "Dashboard", icon: "dashboard"},
  {href: "/shortlist", label: "Top picks", icon: "picks", also: ["/company"]},
  {href: "/portfolio", label: "Portfolio", icon: "portfolio"},
  {href: "/watchlist", label: "Watchlist", icon: "watchlist"},
  {href: "/checks", label: "Thesis checks", icon: "checks"},
  {href: "/scorecard", label: "History", icon: "history"},
  {href: "/snapshots", label: "Snapshots", icon: "snapshots"},
];

function active(path: string, link: typeof LINKS[number]) {
  if (link.href === "/") return path === "/";
  return [link.href, ...(link.also ?? [])].some(h => path === h || path.startsWith(`${h}/`));
}

export function AppShell({children}: {children: ReactNode}) {
  const path = usePathname() ?? "/";
  const router = useRouter();
  const [find, setFind] = useState("");
  const [open, setOpen] = useState(false);
  function search(event: FormEvent) {
    event.preventDefault();
    const q = find.trim();
    router.push(q ? `/shortlist?find=${encodeURIComponent(q)}` : "/shortlist");
  }
  return <div className="app-shell">
    <aside className={`app-sidebar${open ? " open" : ""}`}>
      <Link href="/" className="app-brand" onClick={() => setOpen(false)}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="13" width="4" height="8" rx="1"/><rect x="10" y="9" width="4" height="12" rx="1"/><rect x="17" y="4" width="4" height="17" rx="1"/></svg>
        SignalLens</Link>
      <nav className="app-nav" aria-label="Main">{LINKS.map(l =>
        <Link key={l.href} href={l.href} className={active(path, l) ? "current" : undefined} aria-current={active(path, l) ? "page" : undefined} onClick={() => setOpen(false)}>
          <Icon name={l.icon}/>{l.label}</Link>)}</nav>
      <nav className="app-nav app-nav-bottom" aria-label="Settings">
        <Link href="/portfolio#plan" onClick={() => setOpen(false)}><Icon name="settings"/>Settings</Link></nav>
    </aside>
    <div className="app-main">
      <header className="app-top">
        <button type="button" className="app-menu" aria-label="Menu" aria-expanded={open} onClick={() => setOpen(!open)}>☰</button>
        <form role="search" onSubmit={search} className="app-search"><Icon name="search"/>
          <input type="search" value={find} onChange={e => setFind(e.target.value)} placeholder="Search for a company (e.g. LZ, Western Union)…" aria-label="Search for a company"/></form>
      </header>
      {children}
    </div>
  </div>;
}
