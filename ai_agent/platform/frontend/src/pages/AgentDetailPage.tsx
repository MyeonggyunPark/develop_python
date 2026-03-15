import React, { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  fetchAgentDetail,
  fetchAgentWorkspace,
  finalizeAgentRun,
  regenerateAgentScript,
  requestAgentRecommendations,
  selectAgentTopic,
  startAgentWorkspace,
  submitAgentTopic,
} from "../lib/api";
import type { AgentWorkspace } from "../types";
import { ArtifactItem } from "../components/ArtifactItem";
import { useAsyncData } from "../hooks/useAsyncData";
import { StatusBadge } from "../components/StatusBadge";
import { LoadingCard } from "../components/LoadingCard";
import { ErrorCard } from "../components/ErrorCard";
import { BackButton } from "../components/BackButton";
import { WorkspaceActionButton } from "../components/WorkspaceActionButton";
import { WorkspaceParticipantDesk } from "../components/WorkspaceParticipantDesk";
import { WorkspaceConversationBubble } from "../components/WorkspaceConversationBubble";
import {
  getWorkspaceParticipants,
  getMeetingPreviewParticipants,
  getWorkspaceConversation,
  getMeetingPreviewConversation,
  getActiveParticipantId,
} from "../lib/workspace";
import { getStatusLabel } from "../components/StatusBadge";
import { formatMoney, formatDuration, getTriggerLabel } from "../lib/utils";

export function AgentDetailPage() {
  const { agentSlug = "" } = useParams();
  const [refreshKey, setRefreshKey] = useState(0);
  const [topicInput, setTopicInput] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [agentPanelExpanded, setAgentPanelExpanded] = useState(true);
  const [historyExpanded, setHistoryExpanded] = useState(false);
  const [_workspaceMetaExpanded, setWorkspaceMetaExpanded] = useState(false);
  const [meetingEntered, setMeetingEntered] = useState(false);
  const state = useAsyncData(() => fetchAgentDetail(agentSlug), [agentSlug, refreshKey]);
  const workspaceState = useAsyncData(() => fetchAgentWorkspace(agentSlug), [agentSlug, refreshKey]);
  const workspace = workspaceState.data?.workspace ?? null;

  useEffect(() => {
    if (workspace?.topic) {
      setTopicInput(workspace.topic);
    }
  }, [workspace?.topic]);

  if (state.loading || workspaceState.loading) {
    return <LoadingCard />;
  }

  if (state.error || !state.data || workspaceState.error || !workspaceState.data) {
    return <ErrorCard message={state.error ?? workspaceState.error ?? "데이터가 없습니다."} />;
  }

  const { agent, latest_run: latestRun, cost_total: costTotal } = state.data;
  const isMeetingConnecting = actionLoading === "enter-room";
  const workspaceParticipants = workspace
    ? getWorkspaceParticipants(workspace)
    : meetingEntered
      ? getMeetingPreviewParticipants(isMeetingConnecting)
      : [];
  const workspaceConversation = workspace
    ? getWorkspaceConversation(workspace)
    : meetingEntered
      ? getMeetingPreviewConversation(isMeetingConnecting)
      : [];
  const activeParticipantId = workspace ? getActiveParticipantId(workspace) : meetingEntered ? "ops" : null;
  const participantMap = new Map(workspaceParticipants.map((participant) => [participant.id, participant]));
  const currentDirective = topicInput.trim() || workspace?.topic || "이번 주 웹툰 안건을 입력하면 직원 에이전트가 순서대로 응답합니다.";
  const latestRunSummary = latestRun ? `${latestRun.run_id} · ${getStatusLabel(latestRun.status)}` : "실행 이력 없음";
  const activeParticipant = workspaceParticipants.find((participant) => participant.id === activeParticipantId) ?? null;

  async function runWorkspaceAction(
    actionName: string,
    action: () => Promise<{ workspace: AgentWorkspace | null }>,
  ) {
    setActionError(null);
    setActionLoading(actionName);

    try {
      const result = await action();
      if (result.workspace?.topic) {
        setTopicInput(result.workspace.topic);
      }
      setRefreshKey((value) => value + 1);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "알 수 없는 오류");
    } finally {
      setActionLoading(null);
    }
  }

  async function handleTopicSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!topicInput.trim()) {
      setActionError("주제를 입력해주세요.");
      return;
    }
    await runWorkspaceAction("submit-topic", () => submitAgentTopic(agentSlug, topicInput));
  }

  function handleEnterMeetingRoom() {
    setMeetingEntered(true);
    if (workspace) {
      return;
    }
    void runWorkspaceAction("enter-room", () => startAgentWorkspace(agentSlug));
  }

  return (
    <>
      <BackButton label="Agents로 돌아가기" to="/agents" />
      <section className="card agent-detail-shell">
        <button className="section-toggle" type="button" aria-expanded={agentPanelExpanded} onClick={() => setAgentPanelExpanded((value) => !value)}>
          <div className="section-toggle-main">
            <div className="section-kicker">Agent</div>
            <div className="agent-title-row">
              <h2>{agent.agent_name}</h2>
              <StatusBadge status={agent.status} />
            </div>
            <p>{agent.description}</p>
          </div>
          <span className={`section-toggle-chevron ${agentPanelExpanded ? "is-open" : ""}`}>⌄</span>
        </button>
        {agentPanelExpanded ? (
          <>
            <div className="agent-detail-header">
              <div className="agent-detail-copy">
                <div className="agent-detail-tags">
                  <span className="pill">Slug {agent.agent_slug}</span>
                  <span className="pill">최근 실행 {agent.last_run_at}</span>
                </div>
              </div>
            </div>
            <div className="agent-detail-metrics">
              <article className="agent-detail-metric">
                <span>최근 실행</span>
                <strong>{latestRunSummary}</strong>
              </article>
              <article className="agent-detail-metric">
                <span>최근 트리거</span>
                <strong>{latestRun ? getTriggerLabel(latestRun.trigger_type) : "-"}</strong>
              </article>
              <article className="agent-detail-metric">
                <span>30일 비용</span>
                <strong>{formatMoney(costTotal)}</strong>
              </article>
              <article className="agent-detail-metric">
                <span>7일 실행 수</span>
                <strong>{agent.run_count_7d}</strong>
              </article>
            </div>
          </>
        ) : null}
      </section>

      <section className="card history-card">
        <button className="section-toggle" type="button" aria-expanded={historyExpanded} onClick={() => setHistoryExpanded((value) => !value)}>
          <div className="section-toggle-main">
            <div className="section-kicker">History</div>
            <div className="agent-title-row">
              <h2>실행 이력</h2>
              <span className="pill">{state.data.runs.length} runs</span>
            </div>
            <p>같은 페이지에서 최근 실행 결과와 상태를 바로 확인할 수 있습니다.</p>
          </div>
          <span className={`section-toggle-chevron ${historyExpanded ? "is-open" : ""}`}>⌄</span>
        </button>
        {historyExpanded ? (
          <div className="history-table-wrap">
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
          </div>
        ) : null}
      </section>

      <section className="card workspace-card">
        <div className="card-head workspace-head">
          <div>
            <h2>회의실</h2>
            <p className="muted">회의실에 입장하면 직원 에이전트가 연결되고, 오른쪽 채팅창에서 실행 상태와 지시를 주고받습니다.</p>
          </div>
          {workspace ? (
            <StatusBadge status={workspace.status} />
          ) : meetingEntered ? (
            <StatusBadge className={isMeetingConnecting ? "status-review" : "status-success"} iconName={isMeetingConnecting ? "statusReview" : "statusSuccess"} label={isMeetingConnecting ? "연결 중" : "준비 완료"} />
          ) : (
            <StatusBadge className="status-waiting" iconName="statusWaiting" label="시작 전" />
          )}
        </div>

        {!meetingEntered ? (
          <div className="workspace-lobby">
            <div className="workspace-lobby-copy">
              <div className="section-kicker">Meeting Lobby</div>
              <h3>직원 에이전트 회의실에 입장하세요</h3>
              <p>입장 즉시 오케스트레이터가 회의실을 열고, 주제 전략가, 작가, 에디터 에이전트 호출과 연결 상태 확인을 시작합니다.</p>
            </div>
            <button
              className="workspace-join-button"
              disabled={actionLoading !== null}
              type="button"
              onClick={handleEnterMeetingRoom}
            >
              회의실 입장
            </button>
          </div>
        ) : (
          <div className="workspace-room-shell">
            <div className="workspace-room-layout">
              <section className="workspace-meeting-pane">
                <div className="workspace-meeting-stage">
                  <div className="workspace-stageboard-head">
                    <div>
                      <div className="section-kicker">Meeting Room</div>
                      <h3>직원 에이전트 회의실</h3>
                    </div>
                <div className="workspace-stage-meta">
                      <span className="pill">{activeParticipant ? `${activeParticipant.name} 진행 중` : "연결 대기"}</span>
                      <span className="pill">
                        {workspace
                          ? `${workspaceParticipants.filter((participant) => participant.connectionState === "connected").length}/${workspaceParticipants.length} connected`
                          : isMeetingConnecting
                            ? "연결 확인 중"
                            : "입장 준비"}
                      </span>
                    </div>
                  </div>

                  <div className="workspace-team-grid">
                    {workspaceParticipants.map((participant, index) => (
                      <WorkspaceParticipantDesk
                        key={participant.id}
                        index={index}
                        isActive={participant.id === activeParticipantId}
                        participant={participant}
                      />
                    ))}
                  </div>
                </div>

                <div className="workspace-room-footer">
                  {workspace ? (
                    <article className="workspace-room-panel">
                      <div className="card-head">
                        <h3>운영 감사 로그</h3>
                        <span className="subtle-tag">Live</span>
                      </div>
                      <ul className="timeline-list">
                        {workspace.logs.map((log) => (
                          <li key={`${log.stage}-${log.timestamp}`}>
                            <span className="timeline-dot" />
                            <div>
                              <div className="timeline-head">
                                <strong>{log.stage}</strong>
                                <StatusBadge status={log.status} />
                              </div>
                              <div>{log.message}</div>
                              <div className="muted">{log.timestamp}</div>
                            </div>
                          </li>
                        ))}
                      </ul>
                    </article>
                  ) : (
                    <article className="workspace-room-panel">
                      <div className="card-head">
                        <h3>운영 감사 로그</h3>
                        <span className="subtle-tag">Pending</span>
                      </div>
                      <p className="muted">회의실 연결이 완료되면 실행 로그가 이 영역에 누적됩니다.</p>
                    </article>
                  )}

                  {workspace?.artifacts.length ? (
                    <article className="workspace-room-panel">
                      <div className="card-head">
                        <h3>결과물</h3>
                        <span className="subtle-tag">Output</span>
                      </div>
                      <ul className="artifact-list">
                        {workspace.artifacts.map((artifact) => (
                          <ArtifactItem artifact={artifact} key={`${artifact.artifact_name}-${artifact.version}`} />
                        ))}
                      </ul>
                    </article>
                  ) : null}
                </div>
              </section>

              <aside className="workspace-chat-pane">
                <div className="workspace-chat-head">
                  <div>
                    <div className="section-kicker">Meeting Chat</div>
                    <h3>실행 상태 채팅</h3>
                  </div>
                  <div className="workspace-chat-meta">
                    <span className="pill">{workspace?.run_id ?? "회의실 준비 중"}</span>
                    <span className="pill">{workspace?.topic ?? "브리프 대기"}</span>
                  </div>
                </div>

                <div className="workspace-chat-stream">
                  <div className="workspace-directive-card">
                    <div className="workspace-directive-kicker">사장 지시</div>
                    <strong>{currentDirective}</strong>
                    <p>입력한 지시는 채팅 흐름으로 직원 에이전트에게 전달됩니다.</p>
                  </div>
                  <div className="workspace-conversation-list">
                    {workspaceConversation.map((item, index) => {
                      const participant = participantMap.get(item.speakerId);
                      if (!participant) {
                        return null;
                      }
                      return (
                        <WorkspaceConversationBubble
                          index={index}
                          item={item}
                          key={item.id}
                          participant={participant}
                        />
                      );
                    })}
                  </div>
                </div>

                <div className="workspace-chat-composer">
                  <form className="topic-form" onSubmit={handleTopicSubmit}>
                    <textarea
                      className="topic-input topic-textarea"
                      placeholder="직원들에게 지시할 주제나 요청을 입력하세요."
                      disabled={!workspace || actionLoading !== null}
                      value={topicInput}
                      onChange={(event) => setTopicInput(event.target.value)}
                    />
                    <div className="workspace-chat-actions">
                      <WorkspaceActionButton
                        disabled={!workspace || actionLoading !== null}
                        onClick={() => runWorkspaceAction("recommend", () => requestAgentRecommendations(agentSlug))}
                        tone="secondary"
                      >
                        주제 추천 요청
                      </WorkspaceActionButton>
                      {workspace?.script ? (
                        <WorkspaceActionButton
                          disabled={actionLoading !== null}
                          onClick={() => runWorkspaceAction("regenerate", () => regenerateAgentScript(agentSlug))}
                          tone="secondary"
                        >
                          스크립트 재생성
                        </WorkspaceActionButton>
                      ) : null}
                      {workspace?.script ? (
                        <WorkspaceActionButton
                          disabled={actionLoading !== null}
                          onClick={() => runWorkspaceAction("finalize", () => finalizeAgentRun(agentSlug))}
                        >
                          최종 결과물 생성
                        </WorkspaceActionButton>
                      ) : (
                        <button className="button" disabled={!workspace || actionLoading !== null} type="submit">
                          지시 전송
                        </button>
                      )}
                    </div>
                  </form>

                  {workspace && workspace.recommendations.length > 0 ? (
                    <div className="workspace-block">
                      <div className="workspace-inline-head">
                        <strong>편성 매니저 추천안</strong>
                        <span>{workspace.recommendations.length} options</span>
                      </div>
                      <div className="recommendation-grid">
                        {workspace.recommendations.map((topic) => (
                          <button
                            key={topic}
                            className="recommendation-card"
                            disabled={actionLoading !== null}
                            type="button"
                            onClick={() => {
                              setTopicInput(topic);
                              void runWorkspaceAction("select-topic", () => selectAgentTopic(agentSlug, topic));
                            }}
                          >
                            <strong>{topic}</strong>
                            <span>이 안건으로 회의 확정</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {workspace?.script ? (
                    <div className="workspace-block">
                      <div className="workspace-inline-head">
                        <strong>작가 초안</strong>
                        <span>v{workspace.script.version}</span>
                      </div>
                      <div className="workspace-script-card">
                        <strong>{workspace.script.title}</strong>
                        <p className="muted">{workspace.script.caption}</p>
                      </div>
                    </div>
                  ) : null}

                  {actionError ? <div className="workspace-error">{actionError}</div> : null}
                </div>
              </aside>
            </div>
          </div>
        )}
      </section>
    </>
  );
}
