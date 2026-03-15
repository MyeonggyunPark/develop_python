import React from "react";
import { Link, useParams } from "react-router-dom";
import { fetchAgentDetail, fetchAgentRuns } from "../lib/api";
import { useAsyncData } from "../hooks/useAsyncData";
import { StatusBadge } from "../components/StatusBadge";
import { LoadingCard } from "../components/LoadingCard";
import { ErrorCard } from "../components/ErrorCard";
import { BackButton } from "../components/BackButton";
import { formatMoney, formatDuration, getTriggerLabel } from "../lib/utils";

export function AgentRunsPage() {
  const { agentSlug = "" } = useParams();
  const state = useAsyncData(
    async () => {
      const [agentDetail, runs] = await Promise.all([fetchAgentDetail(agentSlug), fetchAgentRuns(agentSlug)]);
      return { agent: agentDetail.agent, runs: runs.runs };
    },
    [agentSlug],
  );

  if (state.loading) {
    return <LoadingCard />;
  }

  if (state.error || !state.data) {
    return <ErrorCard message={state.error ?? "데이터가 없습니다."} />;
  }

  return (
    <>
      <BackButton label="Overview로 돌아가기" to={`/agents/${agentSlug}`} />
      <section className="section-intro">
        <div>
          <div className="section-kicker">Run History</div>
          <h2>{state.data.agent.agent_name} Run History</h2>
          <p className="muted">에이전트별 실행 결과, 상태, 비용, 소요 시간을 한눈에 확인합니다.</p>
        </div>
      </section>

      <section className="card">
        <table className="table">
          <thead>
            <tr>
              <th>Run ID</th>
              <th>Status</th>
              <th>Trigger</th>
              <th>Started At</th>
              <th>Duration</th>
              <th>Cost</th>
            </tr>
          </thead>
          <tbody>
            {state.data.runs.map((run) => (
              <tr key={run.run_id}>
                <td>
                  <Link to={`/runs/${run.run_id}`}>{run.run_id}</Link>
                </td>
                <td>
                  <StatusBadge status={run.status} />
                </td>
                <td>{getTriggerLabel(run.trigger_type)}</td>
                <td>{run.started_at}</td>
                <td>{formatDuration(run.total_duration_ms)}</td>
                <td>{formatMoney(run.total_cost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
