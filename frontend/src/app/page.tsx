type Ranking = {
  rank: number;
  ticker: string;
  company: string;
  momentum_126d: number;
  evidence: string;
  risk: string;
};

type RankingResponse = {
  vintage_id: string | null;
  as_of: string | null;
  created_at: string | null;
  strategy: string;
  strategy_version: string;
  is_demo: boolean;
  disclaimer: string;
  rankings: Ranking[];
};

type DataStatus = {
  universe_size: number;
  covered_tickers: number;
  row_count: number;
  first_date: string | null;
  last_date: string | null;
  duplicate_rows: number;
  forward_horizon_trading_days: number;
};

const unavailable: RankingResponse = {
  vintage_id: null,
  as_of: null,
  created_at: null,
  strategy: "unavailable",
  strategy_version: "—",
  is_demo: true,
  disclaimer:
    "No published ranking is available. Start the API and publish a vintage to display research results.",
  rankings: [],
};

async function fetchJson<T>(path: string, fallbackValue: T): Promise<T> {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
  try {
    const response = await fetch(`${apiUrl}${path}`, { cache: "no-store" });
    return response.ok ? await response.json() : fallbackValue;
  } catch {
    return fallbackValue;
  }
}

function formatMomentum(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export default async function Home() {
  const [data, status] = await Promise.all([
    fetchJson("/api/v1/rankings/latest", unavailable),
    fetchJson<DataStatus | null>("/api/v1/data/status", null),
  ]);

  return (
    <main>
      <nav>
        <span className="mark">SL</span>
        <strong>SignalLens</strong>
        <span className="tag">
          {data.is_demo ? "Research unavailable" : "Published research"}
        </span>
      </nav>

      <section className="hero">
        <p className="eyebrow">MONTHLY EQUITY RESEARCH</p>
        <h1>
          Find the signal.
          <br />
          <span>Measure what followed.</span>
        </h1>
        <p className="lede">
          A disciplined research workspace for ranking opportunities, preserving
          every prediction, and comparing confidence with reality.
        </p>
      </section>

      <section className="data-status">
        <div>
          <b>{status?.universe_size ?? "—"}</b>
          <span>stocks in universe</span>
        </div>
        <div>
          <b>{status?.covered_tickers ?? "—"}</b>
          <span>with price history</span>
        </div>
        <div>
          <b>{status?.row_count.toLocaleString() ?? "—"}</b>
          <span>daily observations</span>
        </div>
        <div>
          <b>{status?.forward_horizon_trading_days ?? 21}</b>
          <span>trading-day horizon</span>
        </div>
        <div>
          <b>{status?.duplicate_rows ?? "—"}</b>
          <span>duplicate rows</span>
        </div>
      </section>

      <section className="panel">
        <header>
          <div>
            <p className="eyebrow">CURRENT LENS</p>
            <h2>Top three momentum candidates</h2>
          </div>
          <div className="date">
            {data.as_of ? `As of ${data.as_of}` : "No published date"}
          </div>
        </header>

        <div className={data.is_demo ? "notice warning" : "notice"}>
          {data.is_demo ? "Unavailable" : "Immutable published vintage"} ·{" "}
          {data.disclaimer}
        </div>

        {data.rankings.length > 0 ? (
          <div className="grid">
            {data.rankings.map((item) => (
              <article key={item.ticker}>
                <div className="rank">0{item.rank}</div>
                <div className="score">
                  {formatMomentum(item.momentum_126d)}
                  <small> 126D</small>
                </div>
                <h3>{item.ticker}</h3>
                <p className="company">{item.company}</p>
                <p>{item.evidence}</p>
                <p className="risk">
                  <b>Key risk</b>
                  {item.risk}
                </p>
              </article>
            ))}
          </div>
        ) : (
          <div className="empty">
            No synthetic rankings are shown. Publish a real ranking vintage and
            refresh this page.
          </div>
        )}

        <footer>
          Strategy: {data.strategy} v{data.strategy_version}
          {data.vintage_id ? ` · Vintage: ${data.vintage_id}` : ""}
          {" · "}Rankings are research outputs, not instructions to trade.
        </footer>
      </section>
    </main>
  );
}
