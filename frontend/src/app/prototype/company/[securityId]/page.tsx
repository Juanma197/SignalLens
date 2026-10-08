"use client";
import {useEffect,useState} from "react";
import {useParams,useSearchParams} from "next/navigation";
import {Suspense} from "react";
import Link from "next/link";
import {CompanyView,Detail,PrototypeNotice} from "../../view";

function CompanyDetail() {
  const {securityId}=useParams<{securityId:string}>(); const query=useSearchParams();
  const cutoff=query.get("decision_at"), target=query.get("target_members")??"15";
  const key=`${securityId}:${cutoff}:${target}`;
  const [loaded,setLoaded]=useState<{key:string;detail:Detail}|null>(null);
  const [failure,setFailure]=useState<{key:string;error:string}|null>(null);
  const detail=loaded?.key===key?loaded.detail:null;
  const error=!cutoff?"Evidence cutoff is required.":failure?.key===key?failure.error:"";
  useEffect(()=>{
    const controller=new AbortController();
    if(!cutoff)return ()=>controller.abort();
    fetch(`/api/research/prototype/companies/${encodeURIComponent(securityId)}?decision_at=${encodeURIComponent(cutoff)}&target_members=${encodeURIComponent(target)}`,{cache:"no-store",signal:controller.signal}).then(async response=>{
      const value=await response.json(); if(!response.ok)throw new Error(value.detail?.code??"PROTOTYPE_SERVICE_UNAVAILABLE");if(!controller.signal.aborted)setLoaded({key,detail:value});
    }).catch(e=>{if(!controller.signal.aborted)setFailure({key,error:e instanceof Error?e.message:"PROTOTYPE_SERVICE_UNAVAILABLE"});});
    return ()=>controller.abort();
  },[securityId,cutoff,target,key]);
  return <main className="research-page"><nav><span className="mark">SL</span><strong>Prototype company detail</strong><Link href={cutoff?`/prototype?decision_at=${encodeURIComponent(cutoff)}`:"/prototype"}>Back to shortlist</Link></nav><PrototypeNotice/>{error?<p role="alert" className="notice warning">{error}</p>:detail?<CompanyView detail={detail}/>:<p role="status">Reading stored evidence…</p>}</main>;
}
export default function Page(){return <Suspense fallback={<p>Loading company detail…</p>}><CompanyDetail/></Suspense>;}
