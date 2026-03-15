import React, { useState } from "react";
import { fetchDashboard, fetchRunDetail } from "../lib/api";
import { useAsyncData } from "../hooks/useAsyncData";
import { AppIcon } from "../components/AppIcon";
import { StatusBadge } from "../components/StatusBadge";
import { LoadingCard } from "../components/LoadingCard";
import { ErrorCard } from "../components/ErrorCard";
import { DASHBOARD_SUMMARY_ICONS } from "../lib/constants";
import { formatMoney, formatDuration, getTriggerLabel, getStageLabel, getUsageLabel } from "../lib/utils";
import { ArtifactItem } from "../components/ArtifactItem";

export function DashboardPage() {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const state = useAsyncData(fetchDashboard, []);
  const runDetailState = useAsyncData(
    async () => {
      if (!selectedRunId) {
        return null;
      }
      return fetchRunDetail(selectedRunId);
    },
    [selectedRunId],
  );

  if (state.loading) {
    return <LoadingCard />;
  }

  if (state.error || !state.data) {
    return <ErrorCard message={state.error ?? "데이터가 없습니다."} />;
  }

  const data = state.data;

  return (
    <>
      <section className="dashboard-grid">
        <div className="dashboard-main">
          <section className="card-grid">
            {data.summary_cards.map((card, index) => (
              <article className={`card summary-card ${index === 0 ? "summary-card-primary" : ""}`} key={card.label}>
                <div className="summary-icon">
                  <AppIcon name={DASHBOARD_SUMMARY_ICONS[index] ?? "recentCost"} />
                </div>
                <div>
                  <div className="card-label">{card.label}</div>
                  <div className="card-value">{card.value}</div>
                  <div className="card-delta">{card.delta}</div>
                </div>
              </article>
            ))}
          </section>

          <section className="two-column">
            <article className="card">
              <div className="card-head">
                <h2>실행 추이</h2>
              </div>
              <div className="chart-legend">
                <span>
                  <i className="legend-dot legend-dot-accent" />
                  실행
                </span>
                <span>
                  <i className="legend-dot legend-dot-warn" />
                  실패
                </span>
              </div>
              <div className="trend-chart">
                {data.trend_points.map((point) => (
                  <div className="trend-item" key={point.label}>
                    <div className="trend-bars">
                      <span className="trend-bar trend-bar-failed" style={{ height: `${point.failed_height}%` }} />
                      <span className="trend-bar trend-bar-runs" style={{ height: `${point.runs_height}%` }} />
                    </div>
                    <div className="trend-label">{point.label}</div>
                  </div>
                ))}
              </div>
            </article>
            <article className="card">
              <div className="card-head">
                <h2>비용 구성</h2>
              </div>
              <ul className="distribution-list">
                {data.provider_chart.map((row) => (
                  <li key={row.provider}>
                    <div className="distribution-head">
                      <span>{row.provider}</span>
                      <strong>{formatMoney(row.amount)}</strong>
                    </div>
                    <div className="distribution-track">
                      <span className="distribution-fill" style={{ width: `${row.width}%` }} />
                    </div>
                  </li>
                ))}
              </ul>
            </article>
          </section>

          <section className="three-column">
            <article className="card">
              <div className="card-head">
                <h2>최근 실행</h2>
              </div>
              <table className="table interactive-table">
                <thead>
                  <tr>
                    <th>Run ID</th>
                    <th className="table-center">상태</th>
                    <th className="table-center">비용</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent_runs.map((run) => (
                    <tr key={run.run_id} className="is-clickable" onClick={() => setSelectedRunId(run.run_id)}>
                      <td>
                        <span className="run-detail-trigger">{run.run_id}</span>
                      </td>
                      <td className="table-center">
                        <StatusBadge status={run.status} />
                      </td>
                      <td className="table-center">{formatMoney(run.total_cost)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </article>
            <article className="card">
              <div className="card-head">
                <h2>실패 이력</h2>
              </div>
              <table className="table interactive-table">
                <thead>
                  <tr>
                    <th>Run ID</th>
                    <th className="table-center">상태</th>
                  </tr>
                </thead>
                <tbody>
                  {data.failed_runs.length > 0 ? (
                    data.failed_runs.map((run) => (
                      <tr key={run.run_id} className="is-clickable" onClick={() => setSelectedRunId(run.run_id)}>
                        <td>
                          <span className="run-detail-trigger">{run.run_id}</span>
                        </td>
                        <td className="table-center">
                          <StatusBadge status={run.status} />
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td className="table-empty muted" colSpan={2}>
                        실패한 실행이 없습니다.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </article>
            <article className="card">
              <div className="card-head">
                <h2>검토 대기</h2>
              </div>
              <table className="table interactive-table">
                <thead>
                  <tr>
                    <th>Run ID</th>
                    <th className="table-center">상태</th>
                  </tr>
                </thead>
                <tbody>
                  {data.approval_queue.length > 0 ? (
                    data.approval_queue.map((run) => (
                      <tr key={run.run_id} className="is-clickable" onClick={() => setSelectedRunId(run.run_id)}>
                        <td>
                          <span className="run-detail-trigger">{run.run_id}</span>
                        </td>
                        <td className="table-center">
                          <StatusBadge status={run.status} />
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td className="table-empty muted" colSpan={2}>
                        승인 대기 항목이 없습니다.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </article>
          </section>
        </div>

        <aside className="activity-panel">
          <article className="card activity-card">
            <div className="card-head">
              <h2>활동 현황</h2>
            </div>
            <div className="activity-highlight">
              이번 주 에이전트가 절약한 예상 시간은 <strong>3.5시간</strong>입니다.
            </div>
            <div className="activity-metrics">
              <div className="mini-stat">
                <div className="mini-label">총 실행</div>
                <div className="mini-value">{data.activity_stats.total_runs}</div>
              </div>
              <div className="mini-stat">
                <div className="mini-label">실행 요청</div>
                <div className="mini-value">{data.activity_stats.run_requests}</div>
              </div>
              <div className="mini-stat">
                <div className="mini-label">평균 비용</div>
                <div className="mini-value">{data.activity_stats.avg_cost}</div>
              </div>
              <div className="mini-stat">
                <div className="mini-label">검토 대기</div>
                <div className="mini-value">{data.activity_stats.pending_review}</div>
              </div>
            </div>
            <div className="panel-divider" />
            <div className="activity-list">
              <div className="activity-row">
                <span className="activity-dot" />
                <div>
                  <strong>웹툰 자동화 에이전트</strong>
                  <div className="muted">스크립트 검토 대기 실행 1건이 있습니다.</div>
                </div>
              </div>
              <div className="activity-row">
                <span className="activity-dot activity-dot-warn" />
                <div>
                  <strong>비용 알림</strong>
                  <div className="muted">이미지 생성 비용이 지난주 대비 8% 증가했습니다.</div>
                </div>
              </div>
            </div>
            <div className="panel-divider" />
            <div className="activity-usage">
              <div className="card-head compact-head">
                <h3>사용량 요약</h3>
              </div>
              <ul className="metric-list">
                {Object.entries(data.usage_cards).map(([key, value]) => (
                  <li key={key}>
                    <span>{getUsageLabel(key)}</span>
                    <strong>{value}</strong>
                  </li>
                ))}
              </ul>
            </div>
          </article>
        </aside>
      </section>

      {selectedRunId ? (
        <div
          aria-label="배경 클릭으로 닫기"
          className="run-detail-overlay"
          role="presentation"
          onClick={() => setSelectedRunId(null)}
          onKeyDown={(e) => e.key === "Escape" && setSelectedRunId(null)}
        >
          <aside
            aria-label={`실행 상세: ${selectedRunId}`}
            aria-modal="true"
            className="run-detail-drawer"
            role="dialog"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="run-detail-drawer-head">
              <div>
                <div className="section-kicker">Run Detail</div>
                <h3 id="run-detail-title">{selectedRunId}</h3>
                <p className="muted">실행 상세를 같은 화면에서 확인합니다.</p>
              </div>
              <button
                aria-label="실행 상세 닫기"
                className="run-detail-close"
                type="button"
                onClick={() => setSelectedRunId(null)}
              >
                ×
              </button>
            </div>

            {runDetailState.loading ? (
              <div className="run-detail-placeholder">상세 정보를 불러오는 중입니다.</div>
            ) : runDetailState.error || !runDetailState.data ? (
              <div className="run-detail-placeholder error-card">
                {runDetailState.error ?? "실행 상세를 불러오지 못했습니다."}
              </div>
            ) : (
              <>
                <div className="run-detail-summary">
                  <article className="run-detail-stat">
                    <span>상태</span>
                    <div>
                      <StatusBadge status={runDetailState.data.run.status} />
                    </div>
                  </article>
                  <article className="run-detail-stat">
                    <span>트리거</span>
                    <strong>{getTriggerLabel(runDetailState.data.run.trigger_type)}</strong>
                  </article>
                  <article className="run-detail-stat">
                    <span>비용</span>
                    <strong>{formatMoney(runDetailState.data.run.total_cost)}</strong>
                  </article>
                  <article className="run-detail-stat">
                    <span>소요 시간</span>
                    <strong>{formatDuration(runDetailState.data.run.total_duration_ms)}</strong>
                  </article>
                </div>

                {runDetailState.data.run.error_message ? (
                  <div className="run-detail-block">
                    <div className="workspace-inline-head">
                      <strong>실패 사유</strong>
                      <StatusBadge status={runDetailState.data.run.status} />
                    </div>
                    <p className="muted run-detail-message">{runDetailState.data.run.error_message}</p>
                  </div>
                ) : null}

                <div className="run-detail-block">
                  <div className="workspace-inline-head">
                    <strong>실행 로그</strong>
                    <span>{runDetailState.data.logs.length}건</span>
                  </div>
                  <ul className="timeline-list">
                    {runDetailState.data.logs.map((log) => (
                      <li key={`${log.stage}-${log.status}`}>
                        <span className="timeline-dot" />
                        <div>
                          <div className="timeline-head">
                            <strong>{getStageLabel(log.stage)}</strong>
                            <StatusBadge status={log.status} />
                          </div>
                          <div className="muted">{log.message}</div>
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>

                <div className="run-detail-block">
                  <div className="workspace-inline-head">
                    <strong>산출물</strong>
                    <span>{runDetailState.data.artifacts.length}개</span>
                  </div>
                  <ul className="artifact-list">
                    {runDetailState.data.artifacts.length > 0 ? (
                      runDetailState.data.artifacts.map((artifact) => (
                        <ArtifactItem artifact={artifact} key={`${artifact.artifact_name}-${artifact.version}`} />
                      ))
                    ) : (
                      <li className="muted">산출물이 없습니다.</li>
                    )}
                  </ul>
                </div>
              </>
            )}
          </aside>
        </div>
      ) : null}
    </>
  );
}
