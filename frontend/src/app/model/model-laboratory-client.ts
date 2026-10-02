export type PreviewRow={preview_rank:number;qualified_symbol:string;company_name:string;price_percentile:number;price_contribution:number;dilution_percentile:number;dilution_contribution:number;combined_score:number;price_date:string;diluted_share_growth:number;context_score_effect:string};
export type PreviewReport={labels:string[];decision_at:string;model_version:string;configuration_hash:string;registration_timestamp:string;eligible_universe_count:number;scored_count:number;withheld_count:number;preview_entries:PreviewRow[];prospective_vintages_created:number;validation_observations_added:number;recommendations_generated:number};
export type PreviewState={status:"idle"|"loading"|"available"|"error";report?:PreviewReport};
export type ReconstructionState={status:"idle"|"loading"|"available"|"unavailable"|"error";label?:string};
export type LaboratoryState={preview:PreviewState;reconstruction:ReconstructionState};
type Update=(state:LaboratoryState)=>void;

export function safeDecisionTimestamp(value:string):string|null {
  if (!value.trim()) return null;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toISOString() : null;
}

export async function loadModelLaboratory(value:string, update:Update, request:typeof fetch=fetch):Promise<void> {
  const decisionAt=safeDecisionTimestamp(value);
  if (!decisionAt) {
    update({preview:{status:"error"},reconstruction:{status:"idle"}});
    return;
  }

  let state:LaboratoryState={preview:{status:"loading"},reconstruction:{status:"idle"}};
  update(state);
  try {
    const response=await request(`/api/research/model-laboratory/preview?decision_at=${encodeURIComponent(decisionAt)}`,{cache:"no-store"});
    if (!response.ok) throw new Error("preview unavailable");
    const report=await response.json() as PreviewReport;
    state={preview:{status:"available",report},reconstruction:{status:"loading"}};
  } catch {
    state={preview:{status:"error"},reconstruction:{status:"loading"}};
  }
  update(state);

  try {
    const response=await request(`/api/research/model-laboratory/september-reconstruction?decision_at=${encodeURIComponent("2026-09-30T23:59:59Z")}`,{cache:"no-store"});
    if (!response.ok) throw new Error("reconstruction unavailable");
    const result=await response.json() as {reconstruction_status?:unknown};
    const label=typeof result.reconstruction_status === "string" ? result.reconstruction_status : "unavailable";
    state={...state,reconstruction:label === "unavailable" ? {status:"unavailable"} : {status:"available",label}};
  } catch {
    state={...state,reconstruction:{status:"error"}};
  }
  update(state);
}
