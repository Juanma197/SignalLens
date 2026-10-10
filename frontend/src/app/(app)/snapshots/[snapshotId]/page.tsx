"use client";
import {useEffect, useState} from "react";
import {useParams} from "next/navigation";
import Link from "next/link";
import {PrototypeNotice} from "../../view";
import {call, Snapshot, SnapshotView, Tracking} from "../../store-view";

export default function SnapshotPage() {
  const {snapshotId} = useParams<{snapshotId: string}>();
  const [data, setData] = useState<{snapshot: Snapshot; tracking: Tracking}|null>(null);
  const [error, setError] = useState("");
  useEffect(() => {call<{snapshot: Snapshot; tracking: Tracking}>(`snapshots/${encodeURIComponent(snapshotId)}`).then(setData).catch(e => setError(e.message));}, [snapshotId]);
  return <main className="research-page"><nav><span className="mark">SL</span><strong>Snapshot follow-up</strong><Link href="/snapshots">All snapshots</Link></nav><PrototypeNotice/>
    {error ? <p role="alert" className="notice warning">{error}</p> : data ? <SnapshotView {...data}/> : <p role="status">Verifying the snapshot and reading stored prices…</p>}</main>;
}
