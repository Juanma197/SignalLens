type Ranking = { rank: number; ticker: string; company: string; score: number; thesis: string; risk: string };
type RankingResponse = { as_of: string; model: string; is_demo: boolean; disclaimer: string; rankings: Ranking[] };
type DataStatus = { universe_size: number; covered_tickers: number; row_count: number; first_date: string | null; last_date: string | null; duplicate_rows: number; forward_horizon_trading_days: number };

const fallback: RankingResponse = {
  as_of: "2026-09-19", model: "foundation-demo-v0", is_demo: true,
  disclaimer: "Backend unavailable. Showing bundled demonstration data only.",
  rankings: [
    { rank: 1, ticker: "NOVA", company: "Nova Systems", score: 82, thesis: "Synthetic momentum and quality example.", risk: "Synthetic valuation risk." },
    { rank: 2, ticker: "GRID", company: "GridWorks", score: 76, thesis: "Synthetic infrastructure-demand example.", risk: "Synthetic project-delay risk." },
    { rank: 3, ticker: "FLOW", company: "Flow Analytics", score: 71, thesis: "Synthetic recurring-revenue example.", risk: "Synthetic competition risk." },
  ],
};

async function fetchJson<T>(path: string, fallbackValue: T): Promise<T> {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
  try {
    const response = await fetch(`${apiUrl}${path}`, { cache: "no-store" });
    return response.ok ? await response.json() : fallbackValue;
  } catch { return fallbackValue; }
}

export default async function Home() {
  const [data, status] = await Promise.all([
    fetchJson("/api/v1/rankings/demo", fallback),
    fetchJson<DataStatus | null>("/api/v1/data/status", null),
  ]);
  return (
    <main>
      <nav><span className="mark">SL</span><strong>SignalLens</strong><span className="tag">Research preview</span></nav>
      <section className="hero">
        <p className="eyebrow">MONTHLY EQUITY RESEARCH</p>
        <h1>Find the signal.<br /><span>Measure what followed.</span></h1>
        <p className="lede">A disciplined research workspace for ranking opportunities, preserving every prediction, and comparing confidence with reality.</p>
      </section>
      <section className="data-status">
        <div><b>{status?.universe_size ?? "—"}</b><span>stocks in universe</span></div>
        <div><b>{status?.covered_tickers ?? "—"}</b><span>with price history</span></div>
        <div><b>{status?.row_count.toLocaleString() ?? "—"}</b><span>daily observations</span></div>
        <div><b>{status?.forward_horizon_trading_days ?? 21}</b><span>trading-day horizon</span></div>
        <div><b>{status?.duplicate_rows ?? "—"}</b><span>duplicate rows</span></div>
      </section>
      <section className="panel">
        <header><div><p className="eyebrow">CURRENT LENS</p><h2>Top three candidates</h2></div><div className="date">As of {data.as_of}</div></header>
        <div className="notice">Demo mode · {data.disclaimer}</div>
        <div className="grid">{data.rankings.map((item) => (
          <article key={item.ticker}>
            <div className="rank">0{item.rank}</div><div className="score">{item.score}<small>/100</small></div>
            <h3>{item.ticker}</h3><p className="company">{item.company}</p>
            <p>{item.thesis}</p><p className="risk"><b>Key risk</b> {item.risk}</p>
          </article>
        ))}</div>
        <footer>Model: {data.model} · Rankings are research outputs, not instructions to trade.</footer>
      </section>
    </main>
  );
}
