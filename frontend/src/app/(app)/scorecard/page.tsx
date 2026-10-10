"use client";
import {useEffect, useState} from "react";
import Link from "next/link";
import {PrototypeNotice} from "../view";
import {Scorecard, ScorecardView} from "../scorecard-view";
import {Versus, VersusView} from "../versus-view";

export default function Page() {
  const [card, setCard] = useState<Scorecard|null>(null);
  const [error, setError] = useState("");
  const [versus, setVersus] = useState<Versus|null>(null);
  useEffect(() => {
    fetch("/api/research/prototype/scorecard", {cache: "no-store"}).then(async r => {
      const v = await r.json(); if (!r.ok) throw new Error(v.detail?.code ?? "PROTOTYPE_SERVICE_UNAVAILABLE"); setCard(v);
    }).catch(e => setError(e instanceof Error ? e.message : "PROTOTYPE_SERVICE_UNAVAILABLE"));
    // Separate request: a failure here should not hide the scorecard.
    fetch("/api/research/prototype/history/versus-vall", {cache: "no-store"}).then(r => r.ok ? r.json() : null).then(setVersus).catch(() => setVersus(null));
  }, []);
  return <main className="research-page"><nav><span className="mark">SL</span><strong>Scorecard</strong><Link href="/">This month</Link><Link href="/shortlist">Shortlist</Link><Link href="/portfolio">Portfolio</Link><Link href="/checks">Thesis checks</Link></nav>
    <section className="hero compact"><p className="eyebrow">RECORDED CALLS · MEASURED AFTERWARDS</p><h1>Keeping score.<br/><span>Right or wrong, in public.</span></h1>
      <p className="lede">Your money against the same deposits in VALL, then every recorded month&apos;s picks and decisions, measured against the rest of the companies assessed that month after about 1, 3, 6 and 12 months.</p></section>
    <PrototypeNotice/>
    {versus && <VersusView v={versus}/>}
    {error ? <p role="alert" className="notice warning">{error}</p> : card ? <ScorecardView card={card}/> : <p role="status">Scoring recorded months…</p>}</main>;
}
