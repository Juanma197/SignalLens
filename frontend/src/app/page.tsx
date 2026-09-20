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

type PredictionOutcome = {
  ticker: string;
  rank: number;
  status: "pending" | "completed";
  entry_date: string | null;
  exit_date: string | null;
  realized_return: number | null;
  available_post_signal_closes: number;
  required_post_signal_closes: number;
};

type OutcomeResponse = {
  vintage_id: string | null;
  as_of_date: string | null;
  horizon_trading_days: number;
  status: "unavailable" | "pending" | "completed";
  completed_predictions: number;
  total_predictions: number;
  mean_realized_return: number | null;
  outcomes: PredictionOutcome[];
};

type RankingHistoryItem = {
  vintage_id: string;
  as_of_date: string;
  created_at: string;
  strategy: string;
  strategy_version: string;
  tickers: string[];
  status: "pending" | "completed";
  completed_predictions: number;
  total_predictions: number;
  mean_realized_return: number | null;
};

type RankingHistoryResponse = {
  vintages: RankingHistoryItem[];
};

type WatchlistItem = {
  ticker: string;
  company: string;
  note: string;
  added_at: string;
  updated_at: string;
};

type WatchlistResponse = {
  items: WatchlistItem[];
};

type EvidenceItem = {
  evidence_id: string;
  ticker: string;
  evidence_type: "fundamental" | "filing" | "news" | "macro";
  source_name: string;
  source_url: string;
  title: string;
  summary: string;
  published_at: string;
  source_updated_at: string | null;
  retrieved_at: string;
  content_hash: string;
};

type EvidenceResponse = {
  ticker: string;
  as_of: string;
  evidence_type: "fundamental" | "filing" | "news" | "macro";
  status: "fresh" | "stale" | "missing" | "failed";
  max_age_days: number;
  error: string | null;
  items: EvidenceItem[];
};

type FundamentalFact = {
  fact_id: string;
  ticker: string;
  metric:
    | "revenue"
    | "net_income"
    | "eps_diluted"
    | "assets"
    | "liabilities"
    | "cash";
  unit: string;
  value: number;
  period_end: string;
  fiscal_period: string | null;
  form: string;
  source_url: string;
};

type FundamentalsResponse = {
  ticker: string;
  company: string;
  as_of: string;
  status: "complete" | "partial" | "missing";
  expected_metrics: string[];
  missing_metrics: string[];
  facts: FundamentalFact[];
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

const unavailableHistory: RankingHistoryResponse = { vintages: [] };

const unavailableOutcomes: OutcomeResponse = {
  vintage_id: null,
  as_of_date: null,
  horizon_trading_days: 21,
  status: "unavailable",
  completed_predictions: 0,
  total_predictions: 0,
  mean_realized_return: null,
  outcomes: [],
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

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function formatFundamental(fact: FundamentalFact): string {
  if (fact.unit.toLowerCase().includes("shares")) {
    return `${fact.value.toFixed(2)}`;
  }
  const absolute = Math.abs(fact.value);
  const sign = fact.value < 0 ? "-" : "";
  if (absolute >= 1_000_000_000) {
    return `${sign}${(absolute / 1_000_000_000).toFixed(2)}B`;
  }
  if (absolute >= 1_000_000) {
    return `${sign}${(absolute / 1_000_000).toFixed(1)}M`;
  }
  return `${sign}${absolute.toLocaleString()}`;
}

function metricLabel(metric: FundamentalFact["metric"]): string {
  return {
    revenue: "Revenue",
    net_income: "Net income",
    eps_diluted: "Diluted EPS",
    assets: "Assets",
    liabilities: "Liabilities",
    cash: "Cash",
  }[metric];
}

export default async function Home() {
  const [data, status, outcomes, history, watchlist] = await Promise.all([
    fetchJson("/api/v1/rankings/latest", unavailable),
    fetchJson<DataStatus | null>("/api/v1/data/status", null),
    fetchJson("/api/v1/rankings/latest/outcomes", unavailableOutcomes),
    fetchJson("/api/v1/rankings/history", unavailableHistory),
    fetchJson<WatchlistResponse>("/api/v1/watchlist", { items: [] }),
  ]);

  const [evidenceResults, fundamentalsResults] = await Promise.all([
    Promise.all(
      data.rankings.map(async (item) => {
        const fallback: EvidenceResponse = {
          ticker: item.ticker,
          as_of: new Date().toISOString(),
          evidence_type: "filing",
          status: "missing",
          max_age_days: 90,
          error: null,
          items: [],
        };
        const evidence = await fetchJson<EvidenceResponse>(
          `/api/v1/evidence/${encodeURIComponent(item.ticker)}?evidence_type=filing&max_age_days=90`,
          fallback,
        );
        return [item.ticker, evidence] as const;
      }),
    ),
    Promise.all(
      data.rankings.map(async (item) => {
        const fallback: FundamentalsResponse = {
          ticker: item.ticker,
          company: item.company,
          as_of: new Date().toISOString(),
          status: "missing",
          expected_metrics: [],
          missing_metrics: [],
          facts: [],
        };
        const fundamentals = await fetchJson<FundamentalsResponse>(
          `/api/v1/fundamentals/${encodeURIComponent(item.ticker)}`,
          fallback,
        );
        return [item.ticker, fundamentals] as const;
      }),
    ),
  ]);
  const evidenceByTicker = new Map(evidenceResults);
  const fundamentalsByTicker = new Map(fundamentalsResults);

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
        <div><b>{status?.universe_size ?? "—"}</b><span>stocks in universe</span></div>
        <div><b>{status?.covered_tickers ?? "—"}</b><span>with price history</span></div>
        <div><b>{status?.row_count.toLocaleString() ?? "—"}</b><span>daily observations</span></div>
        <div><b>{status?.forward_horizon_trading_days ?? 21}</b><span>trading-day horizon</span></div>
        <div><b>{status?.duplicate_rows ?? "—"}</b><span>duplicate rows</span></div>
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
                  {formatPercent(item.momentum_126d)}
                  <small> 126D</small>
                </div>
                <h3>{item.ticker}</h3>
                <p className="company">{item.company}</p>
                <p>{item.evidence}</p>
                {evidenceByTicker.get(item.ticker)?.items[0] ? (
                  <div className="filing-evidence">
                    <span className={`evidence-status ${evidenceByTicker.get(item.ticker)?.status}`}>
                      SEC filing · {evidenceByTicker.get(item.ticker)?.status}
                    </span>
                    <a
                      href={evidenceByTicker.get(item.ticker)?.items[0].source_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {evidenceByTicker.get(item.ticker)?.items[0].title}
                    </a>
                    <small>
                      Published{" "}
                      {evidenceByTicker
                        .get(item.ticker)
                        ?.items[0].published_at.slice(0, 10)}
                    </small>
                  </div>
                ) : (
                  <div className="filing-evidence unavailable">
                    <span className="evidence-status missing">
                      SEC filing ·{" "}
                      {evidenceByTicker.get(item.ticker)?.status ?? "unavailable"}
                    </span>
                    <small>No filing evidence has been ingested for this ticker.</small>
                  </div>
                )}
                {fundamentalsByTicker.get(item.ticker)?.facts.length ? (
                  <div className="fundamentals">
                    <div className="fundamentals-heading">
                      <span>Latest fundamentals</span>
                      <span className={`coverage ${fundamentalsByTicker.get(item.ticker)?.status}`}>
                        {fundamentalsByTicker.get(item.ticker)?.status}
                      </span>
                    </div>
                    <div className="fundamental-grid">
                      {fundamentalsByTicker
                        .get(item.ticker)
                        ?.facts.filter((fact) =>
                          ["revenue", "net_income", "eps_diluted"].includes(
                            fact.metric,
                          ),
                        )
                        .map((fact) => (
                          <a
                            href={fact.source_url}
                            target="_blank"
                            rel="noreferrer"
                            key={fact.fact_id}
                          >
                            <span>{metricLabel(fact.metric)}</span>
                            <b>{formatFundamental(fact)}</b>
                            <small>
                              {fact.fiscal_period ?? fact.form} · {fact.period_end}
                            </small>
                          </a>
                        ))}
                    </div>
                    {fundamentalsByTicker.get(item.ticker)?.missing_metrics.length ? (
                      <small className="missing-metrics">
                        Missing:{" "}
                        {fundamentalsByTicker
                          .get(item.ticker)
                          ?.missing_metrics.join(", ")}
                      </small>
                    ) : null}
                  </div>
                ) : (
                  <div className="fundamentals unavailable">
                    Structured fundamentals unavailable.
                  </div>
                )}
                <p className="risk"><b>Key risk</b>{item.risk}</p>
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

      <section className="panel outcome-panel">
        <header>
          <div>
            <p className="eyebrow">PREDICTED VS ACTUAL</p>
            <h2>{outcomes.status === "completed" ? "Realized outcome" : "Outcome pending"}</h2>
          </div>
          <div className={`outcome-status ${outcomes.status}`}>
            {outcomes.status}
          </div>
        </header>

        {outcomes.outcomes.length > 0 ? (
          <div className="outcome-list">
            {outcomes.outcomes.map((item) => (
              <div className="outcome-row" key={item.ticker}>
                <div><b>{item.ticker}</b><span>Rank {item.rank}</span></div>
                <div>
                  <b>
                    {item.realized_return === null
                      ? "Pending"
                      : formatPercent(item.realized_return)}
                  </b>
                  <span>
                    {item.status === "pending"
                      ? `${item.available_post_signal_closes} of ${item.required_post_signal_closes} required closes`
                      : `${item.entry_date} to ${item.exit_date}`}
                  </span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="empty">Outcome tracking is unavailable.</div>
        )}

        <footer>
          The evaluation enters at the first close after publication and measures
          the following {outcomes.horizon_trading_days} trading-day return. Stored
          predictions are never rewritten.
        </footer>
      </section>

      <section className="panel watchlist-panel">
        <header>
          <div>
            <p className="eyebrow">PERSONAL RESEARCH</p>
            <h2>Watchlist and notes</h2>
          </div>
          <div className="date">{watchlist.items.length} watched</div>
        </header>

        {watchlist.items.length > 0 ? (
          <div className="watchlist-list">
            {watchlist.items.map((item) => (
              <div className="watchlist-row" key={item.ticker}>
                <div>
                  <b>{item.ticker}</b>
                  <span>{item.company}</span>
                </div>
                <p>{item.note}</p>
              </div>
            ))}
          </div>
        ) : (
          <div className="empty">
            No personal notes yet. Add a universe ticker through the API
            documentation to begin a watchlist.
          </div>
        )}

        <footer>
          Watchlist notes are editable personal research and are kept separate
          from immutable published prediction vintages.
        </footer>
      </section>

      <section className="panel history-panel">
        <header>
          <div>
            <p className="eyebrow">VINTAGE ARCHIVE</p>
            <h2>Published ranking history</h2>
          </div>
          <div className="date">{history.vintages.length} vintages</div>
        </header>

        {history.vintages.length > 0 ? (
          <div className="history-list">
            {history.vintages.map((vintage) => (
              <div className="history-row" key={vintage.vintage_id}>
                <div>
                  <b>{vintage.as_of_date}</b>
                  <span>{vintage.tickers.join(" · ")}</span>
                </div>
                <div>
                  <span className={`outcome-status ${vintage.status}`}>
                    {vintage.status}
                  </span>
                </div>
                <div>
                  <b>
                    {vintage.mean_realized_return === null
                      ? "—"
                      : formatPercent(vintage.mean_realized_return)}
                  </b>
                  <span>mean realized return</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="empty">No published vintage history is available.</div>
        )}

        <footer>
          Every row references an immutable published vintage. Historical
          rankings remain visible after newer months are added.
        </footer>
      </section>
    </main>
  );
}
