from dataclasses import dataclass


@dataclass(frozen=True)
class Security:
    ticker: str
    company: str
    sector: str


UNIVERSE: tuple[Security, ...] = (
    Security("AAPL", "Apple", "Technology"),
    Security("MSFT", "Microsoft", "Technology"),
    Security("NVDA", "NVIDIA", "Technology"),
    Security("GOOGL", "Alphabet", "Communication Services"),
    Security("AMZN", "Amazon", "Consumer Discretionary"),
    Security("META", "Meta Platforms", "Communication Services"),
    Security("AVGO", "Broadcom", "Technology"),
    Security("AMD", "Advanced Micro Devices", "Technology"),
    Security("PLTR", "Palantir Technologies", "Technology"),
    Security("CRM", "Salesforce", "Technology"),
    Security("JPM", "JPMorgan Chase", "Financials"),
    Security("V", "Visa", "Financials"),
    Security("MA", "Mastercard", "Financials"),
    Security("BRK-B", "Berkshire Hathaway", "Financials"),
    Security("LLY", "Eli Lilly", "Health Care"),
    Security("UNH", "UnitedHealth Group", "Health Care"),
    Security("ISRG", "Intuitive Surgical", "Health Care"),
    Security("COST", "Costco", "Consumer Staples"),
    Security("WMT", "Walmart", "Consumer Staples"),
    Security("KO", "Coca-Cola", "Consumer Staples"),
    Security("XOM", "Exxon Mobil", "Energy"),
    Security("NEE", "NextEra Energy", "Utilities"),
    Security("GEV", "GE Vernova", "Industrials"),
    Security("CAT", "Caterpillar", "Industrials"),
    Security("UBER", "Uber Technologies", "Industrials"),
    Security("TSLA", "Tesla", "Consumer Discretionary"),
    Security("NFLX", "Netflix", "Communication Services"),
    Security("SHOP", "Shopify", "Technology"),
    Security("MELI", "MercadoLibre", "Consumer Discretionary"),
    Security("CEG", "Constellation Energy", "Utilities"),
)

TICKERS = tuple(item.ticker for item in UNIVERSE)
FORWARD_HORIZON_TRADING_DAYS = 21
