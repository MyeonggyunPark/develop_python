import React from "react";
import { fetchCosts } from "../lib/api";
import { useAsyncData } from "../hooks/useAsyncData";
import { LoadingCard } from "../components/LoadingCard";
import { ErrorCard } from "../components/ErrorCard";
import { formatMoney } from "../lib/utils";

export function CostsPage() {
  const state = useAsyncData(fetchCosts, []);

  if (state.loading) {
    return <LoadingCard />;
  }

  if (state.error || !state.data) {
    return <ErrorCard message={state.error ?? "데이터가 없습니다."} />;
  }

  const data = state.data;

  return (
    <>
      <section className="card-grid">
        {data.summary_cards.map((card) => (
          <article className="card summary-card" key={card.label}>
            <div>
              <div className="card-label">{card.label}</div>
              <div className="card-value">{card.value}</div>
            </div>
          </article>
        ))}
      </section>

      <section className="section-intro">
        <div>
          <div className="section-kicker">Cost Overview</div>
          <h2>Cost Analytics</h2>
          <p className="muted">Provider별 비용과 에이전트별 비용 흐름을 기준으로 사용량을 추적합니다.</p>
        </div>
      </section>

      <section className="two-column">
        <article className="card">
          <h2>Provider Cost</h2>
          <ul className="metric-list">
            {Object.entries(data.by_provider).map(([provider, amount]) => (
              <li key={provider}>
                <span>{provider}</span>
                <strong>{formatMoney(amount)}</strong>
              </li>
            ))}
          </ul>
        </article>
        <article className="card">
          <h2>Agent Cost</h2>
          <ul className="metric-list">
            {Object.entries(data.by_agent).map(([agentSlug, amount]) => (
              <li key={agentSlug}>
                <span>{agentSlug}</span>
                <strong>{formatMoney(amount)}</strong>
              </li>
            ))}
          </ul>
        </article>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Cost Records</h2>
          <span className="subtle-tag">Raw Records</span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Agent</th>
              <th>Provider</th>
              <th>Service</th>
              <th>Usage</th>
              <th>Cost</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row) => (
              <tr key={`${row.recorded_at}-${row.provider}-${row.service_type}`}>
                <td>{row.recorded_at}</td>
                <td>{row.agent_slug}</td>
                <td>{row.provider}</td>
                <td>{row.service_type}</td>
                <td>
                  {row.usage_amount} {row.usage_unit}
                </td>
                <td>{formatMoney(row.cost_amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
