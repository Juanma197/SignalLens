"use client";
import {useEffect, useState} from "react";
import Link from "next/link";
import {PrototypeNotice} from "../view";
import {Scorecard, ScorecardView} from "../scorecard-view";

export default function Page() {
  const [card, setCard] = useState<Scorecard|null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    fetch("/api/research/prototype/scorecard", {cache: "no-store"}).then(async r => {
      const v = await r.json(); if (!r.ok) throw new Error(v.detail?.code ?? "PROTOTYPE_SERVICE_UNAVAILABLE"); setCard(v);
    }).catch(e => setError(e instanceof Error ? e.message : "PROTOTYPE_SERVICE_UNAVAILABLE"));
  }, []);
  return <main className="research-page"><nav><span className="mark">SL</span><strong>Scorecard</strong><Link href="/prototype/monthly">This month</Link><Link href="/prototype">Shortlist</Link><Link href="/prototype/portfolio">Portfolio</Link><Link href="/prototype/checks">Thesis checks</Link></nav>
    <section className="hero compact"><p className="eyebrow">RECORDED CALLS · MEASURED AFTERWARDS</p><h1>Keeping score.<br/><span>Right or wrong, in public.</span></h1>
      <p className="lede">Every recorded month&apos;s picks and decisions, measured against the rest of the companies assessed that month after about 1, 3, 6 and 12 months.</p></section>
    <PrototypeNotice/>
    {error ? <p role="alert" className="notice warning">{error}</p> : card ? <ScorecardView card={card}/> : <p role="status">Scoring recorded months…</p>}</main>;
}
