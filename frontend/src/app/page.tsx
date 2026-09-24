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
  macro_context: PublishedMacroContext | null;
  fundamental_context: PublishedFundamentalContext | null;
  evidence_context: PublishedEvidenceContext | null;
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

type DataStatus = {
  universe_size: number;
  covered_tickers: number;
  row_count: number;
  first_date: string | null;
  last_date: string | null;
  duplicate_rows: number;
  forward_horizon_trading_days: number;
};

type UniverseCoverage = {
  status: "available" | "stale" | "unavailable";
  listings_discovered: number;
  canonical_companies: number;
  eligible_securities: number;
  excluded_by_reason: Record<string, number>;
  by_country: Record<string, number>;
  by_exchange: Record<string, number>;
  by_currency: Record<string, number>;
  missing: string[];
  stale: string[];
  latest_snapshot: { snapshot_id: string; month: string; snapshot_at: string } | null;
};

type GlobalMarketCoverage = {
  status: "available" | "unavailable";
  price_coverage: Array<{ exchange: string; currency: string; securities: number; latest_trading_date: string }>;
  fx_coverage: Array<{ currency: string; latest_observation_date: string; stale: boolean }>;
  latest_run: { finished_at: string; status: string; failures: number } | null;
};

type MacroObservation = {
  observation_id: string;
  series_id: "FEDFUNDS" | "CPIAUCSL" | "UNRATE" | "DGS10";
  metric: string;
  value: number;
  unit: string;
  frequency: "daily" | "monthly";
  observation_date: string;
  retrieved_at: string;
  source_name: string;
  source_url: string;
  freshness: "fresh" | "stale";
};

type MacroSnapshot = {
  as_of: string;
  status: "complete" | "partial" | "missing";
  expected_series: string[];
  missing_series: string[];
  stale_series: string[];
  observations: MacroObservation[];
};

type PublishedMacroObservation = Omit<MacroObservation, "freshness"> & {
  available_at: string;
};

type PublishedMacroContext = {
  captured_at: string;
  status: "complete" | "partial" | "missing";
  expected_series: string[];
  missing_series: string[];
  observations: PublishedMacroObservation[];
};

type PublishedFundamentalTickerContext = {
  ticker: string;
  status: "complete" | "partial" | "missing";
  expected_metrics: string[];
  missing_metrics: string[];
  facts: FundamentalFact[];
};

type PublishedFundamentalContext = {
  captured_at: string;
  tickers: PublishedFundamentalTickerContext[];
};

type PublishedEvidenceTypeContext = {
  status: "fresh" | "stale" | "missing" | "failed";
  max_age_days: number;
  error: string | null;
  items: EvidenceItem[];
};

type PublishedTickerEvidenceContext = {
  ticker: string;
  filing: PublishedEvidenceTypeContext;
  news: PublishedEvidenceTypeContext;
};

type PublishedEvidenceContext = {
  captured_at: string;
  tickers: PublishedTickerEvidenceContext[];
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
  macro_context: null,
  fundamental_context: null,
  evidence_context: null,
  rankings: [],
};

const unavailableHistory: RankingHistoryResponse = { vintages: [] };

const unavailableMacro: MacroSnapshot = {
  as_of: new Date(0).toISOString(),
  status: "missing",
  expected_series: ["FEDFUNDS", "CPIAUCSL", "UNRATE", "DGS10"],
  missing_series: ["FEDFUNDS", "CPIAUCSL", "UNRATE", "DGS10"],
  stale_series: [],
  observations: [],
};

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
  const apiUrl =
    process.env.SIGNALLENS_API_URL ??
    process.env.NEXT_PUBLIC_API_URL ??
    "http://127.0.0.1:8000";
  const apiToken = process.env.SIGNALLENS_API_TOKEN;
  const headers = apiToken
    ? { Authorization: `Bearer ${apiToken}` }
    : undefined;
  try {
    const response = await fetch(`${apiUrl}${path}`, {
      cache: "no-store",
      headers,
    });
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

function macroLabel(seriesId: MacroObservation["series_id"]): string {
  return {
    FEDFUNDS: "Federal funds rate",
    CPIAUCSL: "Consumer price index",
    UNRATE: "Unemployment rate",
    DGS10: "10-year Treasury yield",
  }[seriesId];
}

function macroContext(seriesId: MacroObservation["series_id"]): string {
  return {
    FEDFUNDS:
      "Policy-rate context. Higher rates can increase financing and discount rates.",
    CPIAUCSL:
      "Price-level context. The direction of change matters more than the index alone.",
    UNRATE:
      "Labour-market context. Interpret changes alongside growth and inflation.",
    DGS10:
      "Long-term rate context. Higher yields can pressure valuations and borrowing costs.",
  }[seriesId];
}

function formatMacro(item: Pick<MacroObservation, "unit" | "value">): string {
  return item.unit === "percent"
    ? `${item.value.toFixed(2)}%`
    : item.value.toFixed(3);
}

export default async function Home() {
  const [data, status, outcomes, history, watchlist, macro, universeCoverage, marketCoverage] =
    await Promise.all([
      fetchJson("/api/v1/rankings/latest", unavailable),
      fetchJson<DataStatus | null>("/api/v1/data/status", null),
      fetchJson("/api/v1/rankings/latest/outcomes", unavailableOutcomes),
      fetchJson("/api/v1/rankings/history", unavailableHistory),
      fetchJson<WatchlistResponse>("/api/v1/watchlist", { items: [] }),
      fetchJson<MacroSnapshot>("/api/v1/macro/latest", unavailableMacro),
      fetchJson<UniverseCoverage>("/api/v1/universe/coverage", {
        status: "unavailable", listings_discovered: 0, canonical_companies: 0,
        eligible_securities: 0, excluded_by_reason: {}, by_country: {}, by_exchange: {},
        by_currency: {}, missing: ["coverage_api"], stale: [], latest_snapshot: null,
      }),
      fetchJson<GlobalMarketCoverage>("/api/v1/universe/market-data-coverage", {
        status: "unavailable", price_coverage: [], fx_coverage: [], latest_run: null,
      }),
    ]);

  const evidenceByTicker = new Map(
    (data.evidence_context?.tickers ?? []).map(
      (item) => [item.ticker, item.filing] as const,
    ),
  );
  const newsByTicker = new Map(
    (data.evidence_context?.tickers ?? []).map(
      (item) => [item.ticker, item.news] as const,
    ),
  );
  const fundamentalsByTicker = new Map(
    (data.fundamental_context?.tickers ?? []).map(
      (item) => [item.ticker, item] as const,
    ),
  );

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

      <section className="panel universe-panel">
        <header>
          <div>
            <p className="eyebrow">GLOBAL UNIVERSE · SHADOW RESEARCH</p>
            <h2>Discovery coverage</h2>
          </div>
          <span className={`macro-coverage ${universeCoverage.status === "available" ? "complete" : universeCoverage.status === "stale" ? "partial" : "missing"}`}>
            {universeCoverage.status}
          </span>
        </header>
        <div className="coverage-grid">
          <div><b>{universeCoverage.listings_discovered.toLocaleString()}</b><span>listings discovered</span></div>
          <div><b>{universeCoverage.canonical_companies.toLocaleString()}</b><span>canonical companies</span></div>
          <div><b>{universeCoverage.eligible_securities.toLocaleString()}</b><span>eligible securities</span></div>
          <div><b>{Object.values(universeCoverage.excluded_by_reason).reduce((sum, count) => sum + count, 0).toLocaleString()}</b><span>exclusion decisions</span></div>
        </div>
        <p className="notice warning">
          Research infrastructure only. The live 30-stock momentum_126d universe and published vintages are unchanged.
          {universeCoverage.latest_snapshot ? ` Latest immutable snapshot: ${universeCoverage.latest_snapshot.month}.` : " No monthly snapshot has been created."}
        </p>
        <footer>
          Countries: {Object.keys(universeCoverage.by_country).length} · Exchanges: {Object.keys(universeCoverage.by_exchange).length} · Currencies: {Object.keys(universeCoverage.by_currency).length}
          {` · Priced exchanges: ${marketCoverage.price_coverage.length} · FX currencies: ${marketCoverage.fx_coverage.length}`}
          {marketCoverage.fx_coverage.some((item) => item.stale) ? " · FX stale" : ""}
          {marketCoverage.latest_run ? ` · Latest ingestion: ${marketCoverage.latest_run.status} (${marketCoverage.latest_run.failures} failures)` : " · Price/FX ingestion unavailable"}
          {universeCoverage.missing.length ? ` · Missing: ${universeCoverage.missing.join(", ")}` : ""}
          {universeCoverage.stale.length ? ` · Stale: ${universeCoverage.stale.join(", ")}` : ""}
        </footer>
      </section>

      <section className="panel macro-panel">
        <header>
          <div>
            <p className="eyebrow">MACRO ENVIRONMENT</p>
            <h2>Current economic context</h2>
          </div>
          <div className={`macro-coverage ${macro.status}`}>
            {macro.status}
          </div>
        </header>

        {macro.observations.length > 0 ? (
          <div className="macro-grid">
            {macro.observations.map((item) => (
              <a
                href={item.source_url}
                target="_blank"
                rel="noreferrer"
                className="macro-card"
                key={item.observation_id}
              >
                <div className="macro-card-heading">
                  <span>{macroLabel(item.series_id)}</span>
                  <span className={`freshness ${item.freshness}`}>
                    {item.freshness}
                  </span>
                </div>
                <b>{formatMacro(item)}</b>
                <small>
                  {item.frequency} · observation {item.observation_date}
                </small>
                <p>{macroContext(item.series_id)}</p>
                <span className="macro-source">FRED source ↗</span>
              </a>
            ))}
          </div>
        ) : (
          <div className="empty">
            Macro observations are unavailable. Run the FRED ingestion command
            and refresh this page.
          </div>
        )}

        <footer>
          Macro indicators provide context, not a buy or sell signal. Values are
          shown with their observation dates and become usable only after
          SignalLens retrieves them.
          {macro.missing_series.length
            ? ` Missing: ${macro.missing_series.join(", ")}.`
            : ""}
          {macro.stale_series.length
            ? ` Stale: ${macro.stale_series.join(", ")}.`
            : ""}
        </footer>
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

        {data.macro_context ? (
          <div className="vintage-macro">
            <div className="vintage-macro-heading">
              <div>
                <b>Macro context frozen with this vintage</b>
                <span>
                  Captured {data.macro_context.captured_at.slice(0, 10)}
                </span>
              </div>
              <span className={`macro-coverage ${data.macro_context.status}`}>
                {data.macro_context.status}
              </span>
            </div>
            <div className="vintage-macro-values">
              {data.macro_context.observations.map((item) => (
                <a
                  href={item.source_url}
                  target="_blank"
                  rel="noreferrer"
                  key={item.observation_id}
                >
                  <span>{macroLabel(item.series_id)}</span>
                  <b>{formatMacro(item)}</b>
                  <small>{item.observation_date}</small>
                </a>
              ))}
            </div>
          </div>
        ) : null}

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
                    <small>No filing was captured with this vintage.</small>
                  </div>
                )}
                {newsByTicker.get(item.ticker)?.items.length ? (
                  <div className="news-evidence">
                    <div className="news-heading">
                      <span>News frozen with vintage</span>
                      <span className={`evidence-status ${newsByTicker.get(item.ticker)?.status}`}>
                        {newsByTicker.get(item.ticker)?.status}
                      </span>
                    </div>
                    {newsByTicker
                      .get(item.ticker)
                      ?.items.slice(0, 2)
                      .map((newsItem) => (
                        <a
                          href={newsItem.source_url}
                          target="_blank"
                          rel="noreferrer"
                          key={newsItem.evidence_id}
                        >
                          <span>{newsItem.title}</span>
                          <small>
                            {newsItem.source_name.replace("Google News RSS / ", "")}
                            {" · "}
                            {newsItem.published_at.slice(0, 10)}
                          </small>
                        </a>
                      ))}
                  </div>
                ) : (
                  <div className="news-evidence unavailable">
                    <div className="news-heading">
                      <span>Recent company news</span>
                      <span className="evidence-status missing">
                        {newsByTicker.get(item.ticker)?.status ?? "unavailable"}
                      </span>
                    </div>
                    <small>No news was captured with this vintage.</small>
                  </div>
                )}
                {fundamentalsByTicker.get(item.ticker)?.facts.length ? (
                  <div className="fundamentals">
                    <div className="fundamentals-heading">
                      <span>Fundamentals frozen with vintage</span>
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
