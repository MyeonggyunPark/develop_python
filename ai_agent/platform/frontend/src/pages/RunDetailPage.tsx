import React from "react";
import { useParams } from "react-router-dom";
import { fetchRunDetail } from "../lib/api";
import { useAsyncData } from "../hooks/useAsyncData";
import { StatusBadge } from "../components/StatusBadge";
import { LoadingCard } from "../components/LoadingCard";
import { ErrorCard } from "../components/ErrorCard";
import { ArtifactItem } from "../components/ArtifactItem";
import { formatMoney } from "../lib/utils";

export function RunDetailPage() {
  const { runId = "" } = useParams();
  const state = useAsyncData(() => fetchRunDetail(runId), [runId]);

  if (state.loading) {
    return <LoadingCard />;
  }

  if (state.error || !state.data) {
    return <ErrorCard message={state.error ?? "데이터가 없습니다."} />;
  }

  const { run, logs, artifacts } = state.data;

  return (
    <>
      <section className="detail-hero">
        <article className="card detail-hero-main">
          <div className="section-kicker">Run Summary</div>
          <h2>Run Summary</h2>
          <ul className="stack-list">
            <li>
              <strong>Run ID:</strong> {run.run_id}
            </li>
            <li>
              <strong>Agent:</strong> {run.agent_name}
            </li>
            <li>
              <strong>Status:</strong> <StatusBadge status={run.status} />
            </li>
            <li>
              <strong>Started At:</strong> {run.started_at}
            </li>
            <li>
              <strong>Ended At:</strong> {run.ended_at ?? "-"}
            </li>
            <li>
              <strong>Cost:</strong> {formatMoney(run.total_cost)}
            </li>
          </ul>
        </article>
        <article className="card detail-side-card">
          <div className="card-head">
            <h2>Error Info</h2>
            <span className="subtle-tag">Debug</span>
          </div>
          <ul className="stack-list">
            <li>
              <strong>Stage:</strong> {run.error_stage ?? "-"}
            </li>
            <li>
              <strong>Type:</strong> {run.error_type ?? "-"}
            </li>
            <li>
              <strong>Message:</strong> {run.error_message ?? "-"}
            </li>
          </ul>
        </article>
      </section>

      <section className="two-column">
        <article className="card">
          <div className="card-head">
            <h2>Logs</h2>
            <span className="subtle-tag">Timeline</span>
          </div>
          <ul className="timeline-list">
            {logs.map((log) => (
              <li key={`${log.stage}-${log.status}`}>
                <span className="timeline-dot" />
                <div>
                  <div className="timeline-head">
                    <strong>{log.stage}</strong>
                    <StatusBadge status={log.status} />
                  </div>
                  <div className="muted">{log.message}</div>
                </div>
              </li>
            ))}
          </ul>
        </article>
        <article className="card">
          <div className="card-head">
            <h2>Artifacts</h2>
            <span className="subtle-tag">Artifacts</span>
          </div>
          <ul className="artifact-list">
            {artifacts.length > 0 ? (
              artifacts.map((artifact) => (
                <ArtifactItem artifact={artifact} key={`${artifact.artifact_name}-${artifact.version}`} />
              ))
            ) : (
              <li className="muted">산출물이 없습니다.</li>
            )}
          </ul>
        </article>
      </section>
    </>
  );
}
