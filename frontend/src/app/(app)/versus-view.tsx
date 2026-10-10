import {money} from "./portfolio-view";
import {percent} from "./view";

type Flow = {on: string; amount: number; fund_price: number; price_on: string; gbp_per_usd: number; units: number};
export type Versus = {fund: string; fund_label: string; as_of: string; price_on: string|null; net_deposited: number; your_value: number|null; fund_value: number|null;
  difference: number|null; your_money_weighted: number|null; fund_money_weighted: number|null; complete: boolean; flows: Flow[];
  unpriced_flows: {on: string; amount: number; missing: string}[]; method: string; caveats: string[]; note?: string; label: string};

const gbp = (v: number|null|undefined) => v == null ? "—" : money(v, "GBP");
const yearly = (v: number|null|undefined) => v == null ? "—" : `${v > 0 ? "+" : ""}${percent(v)} a year`;

/** The pre-registered test, on your own account: your holdings plus cash against
 *  the same deposits, on the same days, in VALL. */
export function VersusView({v}: {v: Versus}) {
  const ahead = v.difference != null && v.difference >= 0;
  return <section className="panel prototype-panel"><p className="eyebrow">YOUR MONEY · SAME DEPOSITS IN VALL</p><h2>You vs VALL</h2>
    {v.difference != null
      ? <p className={`versus-headline ${ahead ? "gain" : "loss"}`}>{ahead ? "Ahead" : "Behind"} by {gbp(Math.abs(v.difference))}</p>
      : <p className="versus-headline">{v.note ?? "Not comparable yet: a price or rate is missing (details below)."}</p>}
    <dl className="versus-grid">
      <div><dt>You (holdings + cash)</dt><dd>{gbp(v.your_value)}</dd><dd><small>{yearly(v.your_money_weighted)}</small></dd></div>
      <div><dt>Same deposits in VALL</dt><dd>{gbp(v.fund_value)}</dd><dd><small>{yearly(v.fund_money_weighted)}</small></dd></div>
      <div><dt>Deposited, net</dt><dd>{gbp(v.net_deposited)}</dd></div>
    </dl>
    {v.unpriced_flows.length > 0 && <p className="notice warning">{v.unpriced_flows.length} deposit(s) have no stored {v.unpriced_flows[0].missing} near their date, so the VALL side is not valued.</p>}
    <details className="pick-details"><summary>Show deposits and method</summary>
      {v.flows.length > 0 && <div className="prototype-table-wrap"><table><thead><tr><th>Date</th><th>Pounds</th><th>{v.fund} close</th><th>£ per $</th><th>Units</th></tr></thead><tbody>
        {v.flows.map(f => <tr key={f.on + f.amount}><td>{f.on}</td><td>{gbp(f.amount)}</td><td>${f.fund_price.toFixed(2)}{f.price_on !== f.on && <small>{f.price_on}</small>}</td><td>{f.gbp_per_usd.toFixed(4)}</td><td>{f.units.toFixed(4)}</td></tr>)}
      </tbody></table></div>}
      <p><small>{v.method}</small></p><ul>{v.caveats.map(c => <li key={c}><small>{c}</small></li>)}</ul><p><small>{v.label}</small></p>
    </details>
  </section>;
}
