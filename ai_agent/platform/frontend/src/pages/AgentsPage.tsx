import React from "react";
import { Link } from "react-router-dom";
import { fetchAgents } from "../lib/api";
import { useAsyncData } from "../hooks/useAsyncData";
import { StatusBadge } from "../components/StatusBadge";
import { LoadingCard } from "../components/LoadingCard";
import { ErrorCard } from "../components/ErrorCard";
import { getAgentInitials } from "../lib/utils";

export function AgentsPage() {
  const state = useAsyncData(fetchAgents, []);

  if (state.loading) {
    return <LoadingCard />;
  }

  if (state.error || !state.data) {
    return <ErrorCard message={state.error ?? "데이터가 없습니다."} />;
  }

  return (
    <>
      <section className="agent-card-grid">
        {state.data.agents.map((agent) => (
          <article className="card agent-card" key={agent.agent_id}>
            <Link className="agent-card-link" to={`/agents/${agent.agent_slug}`}>
              <div className="agent-card-status">
                <StatusBadge status={agent.status} />
              </div>
              <div className="agent-card-image">
                {agent.image_url ? (
                  <img alt={agent.agent_name} className="agent-card-photo" src={agent.image_url} />
                ) : (
                  <div className="agent-card-placeholder">
                    <span>{getAgentInitials(agent.agent_name)}</span>
                    <small>Image Slot</small>
                  </div>
                )}
              </div>
              <div className="agent-card-body">
                <div className="agent-card-header">
                  <div>
                    <div className="agent-row-title">{agent.agent_name}</div>
                    <div className="agent-card-subtitle muted">{agent.description}</div>
                  </div>
                </div>
              </div>
            </Link>
          </article>
        ))}
      </section>
    </>
  );
}
