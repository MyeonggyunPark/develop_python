import { type CSSProperties, type FormEvent, useEffect, useState } from "react";
import {
  BrowserRouter,
  Link,
  NavLink,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
import {
  fetchAgentDetail,
  fetchAgentWorkspace,
  fetchAgentRuns,
  fetchAgents,
  fetchCosts,
  fetchDashboard,
  fetchHealth,
  finalizeAgentRun,
  regenerateAgentScript,
  requestAgentRecommendations,
  fetchRunDetail,
  fetchSettings,
  selectAgentTopic,
  startAgentWorkspace,
  submitAgentTopic,
} from "./lib/api";
import type { AgentWorkspace, Artifact } from "./types";

const STATUS_LABELS: Record<string, string> = {
  active: "활성",
  idle: "대기",
  script_review: "검토 중",
  approved: "승인됨",
  posted: "게시 완료",
  failed: "실패",
  rejected: "반려됨",
  disabled: "비활성",
  done: "완료",
  waiting: "대기 중",
  awaiting_topic_input: "주제 입력 대기",
  topic_recommended: "추천안 준비",
};

const TRIGGER_LABELS: Record<string, string> = {
  manual: "Manual",
  scheduled: "Scheduled",
};

type AsyncState<T> = {
  data: T | null;
  loading: boolean;
  error: string | null;
};

type WorkspaceParticipant = {
  id: "ops" | "topic" | "writer" | "qa";
  name: string;
  title: string;
  status: string;
  note: string;
  badge: string;
  tone: "ops" | "topic" | "writer" | "qa";
  avatarCode: string;
  avatarAccent: string;
  avatarSrc: string;
  avatarPosition: string;
  avatarScale: number;
  connectionState: "offline" | "connecting" | "connected";
  connectionLabel: string;
};

type WorkspaceConversationItem = {
  id: string;
  speakerId: WorkspaceParticipant["id"];
  speakerName: string;
  speakerTitle: string;
  tone: "staff" | "system" | "success";
  message: string;
  meta: string;
};

type WorkspacePhase = {
  id: string;
  label: string;
  owner: string;
  detail: string;
  state: "done" | "active" | "upcoming";
};

type IconName =
  | "dashboard"
  | "agents"
  | "costs"
  | "settings"
  | "activeAgents"
  | "recentRuns"
  | "failedRuns"
  | "recentCost"
  | "refresh"
  | "statusSuccess"
  | "statusReview"
  | "statusFailed"
  | "statusWaiting"
  | "statusDisabled"
  | "statusDefault";

const WORKSPACE_PHASE_TEMPLATES = [
  { id: "brief", label: "브리프 접수", owner: "사장", detail: "이번 주 방향과 마감 공유" },
  { id: "topic", label: "주제 확정", owner: "편성", detail: "직접 입력 또는 추천 선택" },
  { id: "script", label: "스크립트 작성", owner: "작가", detail: "4컷 흐름과 캡션 초안 작성" },
  { id: "review", label: "교정 및 승인", owner: "에디터", detail: "재생성 여부 확인 후 승인" },
  { id: "output", label: "결과물 전달", owner: "운영", detail: "최종 산출물 정리 및 공유" },
] as const;

const DASHBOARD_SUMMARY_ICONS: IconName[] = ["activeAgents", "recentRuns", "failedRuns", "recentCost"];

function AppIcon({ name }: { name: IconName }) {
  const commonProps = {
    fill: "none",
    stroke: "currentColor",
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    strokeWidth: 1.9,
  };

  switch (name) {
    case "dashboard":
      return (
        <svg aria-hidden="true" className="app-icon" viewBox="0 0 24 24">
          <rect {...commonProps} x="3.5" y="4" width="7" height="7" rx="1.8" />
          <rect {...commonProps} x="13.5" y="4" width="7" height="5" rx="1.8" />
          <rect {...commonProps} x="3.5" y="14" width="7" height="6" rx="1.8" />
          <rect {...commonProps} x="13.5" y="12" width="7" height="8" rx="1.8" />
        </svg>
      );
    case "agents":
      return (
        <svg aria-hidden="true" className="app-icon" viewBox="0 0 24 24">
          <rect {...commonProps} x="5.5" y="6" width="13" height="10" rx="4" />
          <path {...commonProps} d="M9 6V4.5M15 6V4.5M9.5 10.5h.01M14.5 10.5h.01M9 13.5c1 .8 2 .8 3 .8s2 0 3-.8" />
          <path {...commonProps} d="M9 16v2.5M15 16v2.5" />
        </svg>
      );
    case "costs":
      return (
        <svg aria-hidden="true" className="app-icon" viewBox="0 0 24 24">
          <ellipse {...commonProps} cx="8.5" cy="8.5" rx="4.5" ry="2.2" />
          <path {...commonProps} d="M4 8.5v3.2c0 1.2 2 2.2 4.5 2.2s4.5-1 4.5-2.2V8.5" />
          <ellipse {...commonProps} cx="15.5" cy="14.8" rx="4.5" ry="2.2" />
          <path {...commonProps} d="M11 14.8V18c0 1.2 2 2.2 4.5 2.2S20 19.2 20 18v-3.2" />
        </svg>
      );
    case "settings":
      return (
        <svg aria-hidden="true" className="app-icon" viewBox="0 0 24 24">
          <circle {...commonProps} cx="12" cy="12" r="3.1" />
          <path
            {...commonProps}
            d="M12 3.2 13.6 4l1.8-.6 1.2 1.2-.6 1.8.8 1.6 1.8.7v1.8l-1.8.7-.8 1.6.6 1.8-1.2 1.2-1.8-.6-1.6.8-.7 1.8h-1.8l-.7-1.8-1.6-.8-1.8.6-1.2-1.2.6-1.8-.8-1.6-1.8-.7V9.3l1.8-.7.8-1.6-.6-1.8 1.2-1.2 1.8.6 1.6-.8.7-1.8h1.8Z"
          />
        </svg>
      );
    case "activeAgents":
      return (
        <svg aria-hidden="true" className="app-icon" viewBox="0 0 24 24">
          <path {...commonProps} d="M12 4.2v6.1" />
          <path {...commonProps} d="M7.5 6.6a7 7 0 1 0 9 0" />
        </svg>
      );
    case "recentRuns":
      return (
        <svg aria-hidden="true" className="app-icon" viewBox="0 0 24 24">
          <circle {...commonProps} cx="12" cy="12" r="8" />
          <path {...commonProps} d="m10 8 5 4-5 4V8Z" />
        </svg>
      );
    case "failedRuns":
      return (
        <svg aria-hidden="true" className="app-icon" viewBox="0 0 24 24">
          <path {...commonProps} d="M12 5.5 19.5 18a1 1 0 0 1-.86 1.5H5.36A1 1 0 0 1 4.5 18L12 5.5Z" />
          <path {...commonProps} d="M12 10v4.5M12 17.2h.01" />
        </svg>
      );
    case "recentCost":
      return <AppIcon name="costs" />;
    case "refresh":
      return (
        <svg aria-hidden="true" className="app-icon" viewBox="0 0 24 24">
          <path {...commonProps} d="M20 7v5h-5" />
          <path {...commonProps} d="M19 12a7 7 0 1 1-2.1-5" />
        </svg>
      );
    case "statusSuccess":
      return (
        <svg aria-hidden="true" className="app-icon" viewBox="0 0 24 24">
          <path {...commonProps} d="m7.5 12.5 3 3 6-7" />
        </svg>
      );
    case "statusReview":
      return (
        <svg aria-hidden="true" className="app-icon" viewBox="0 0 24 24">
          <circle {...commonProps} cx="11" cy="11" r="4.5" />
          <path {...commonProps} d="m14.5 14.5 4 4" />
        </svg>
      );
    case "statusFailed":
      return (
        <svg aria-hidden="true" className="app-icon" viewBox="0 0 24 24">
          <path {...commonProps} d="M8 8l8 8M16 8l-8 8" />
        </svg>
      );
    case "statusWaiting":
      return (
        <svg aria-hidden="true" className="app-icon" viewBox="0 0 24 24">
          <circle {...commonProps} cx="12" cy="12" r="7" />
          <path {...commonProps} d="M12 8v4.2l2.6 1.8" />
        </svg>
      );
    case "statusDisabled":
      return (
        <svg aria-hidden="true" className="app-icon" viewBox="0 0 24 24">
          <circle {...commonProps} cx="12" cy="12" r="7" />
          <path {...commonProps} d="M8 16 16 8" />
        </svg>
      );
    case "statusDefault":
      return (
        <svg aria-hidden="true" className="app-icon" viewBox="0 0 24 24">
          <circle {...commonProps} cx="12" cy="12" r="2.4" />
        </svg>
      );
  }
}

function useAsyncData<T>(loader: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({
    data: null,
    loading: true,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;

    async function run() {
      setState({ data: null, loading: true, error: null });

      try {
        const data = await loader();
        if (!cancelled) {
          setState({ data, loading: false, error: null });
        }
      } catch (error) {
        if (!cancelled) {
          setState({
            data: null,
            loading: false,
            error: error instanceof Error ? error.message : "알 수 없는 오류",
          });
        }
      }
    }

    run();

    return () => {
      cancelled = true;
    };
  }, deps);

  return state;
}

function formatMoney(value: number) {
  return `$${value.toFixed(2)}`;
}

function formatDuration(totalDurationMs: number) {
  return `${(totalDurationMs / 60000).toFixed(1)} min`;
}

function getStatusLabel(status: string) {
  return STATUS_LABELS[status] ?? status;
}

function getStatusBadgeMeta(status: string): { className: string; icon: IconName; label: string } {
  const label = getStatusLabel(status);

  if (["active", "approved", "posted", "done"].includes(status)) {
    return { className: "status-success", icon: "statusSuccess", label };
  }
  if (["script_review", "topic_recommended"].includes(status)) {
    return { className: "status-review", icon: "statusReview", label };
  }
  if (["failed", "rejected"].includes(status)) {
    return { className: "status-failed", icon: "statusFailed", label };
  }
  if (["waiting", "awaiting_topic_input", "idle"].includes(status)) {
    return { className: "status-waiting", icon: "statusWaiting", label };
  }
  if (status === "disabled") {
    return { className: "status-disabled", icon: "statusDisabled", label };
  }

  return { className: "status-default", icon: "statusDefault", label };
}

function StatusBadge({
  status,
  className,
  iconName,
  label,
}: {
  status?: string;
  className?: string;
  iconName?: IconName;
  label?: string;
}) {
  const meta = status ? getStatusBadgeMeta(status) : null;
  const badgeClassName = `badge badge-icon ${className ?? meta?.className ?? "status-default"}`;
  const badgeLabel = label ?? meta?.label ?? "";
  const badgeIconName = iconName ?? meta?.icon ?? "statusDefault";

  return (
    <span aria-label={badgeLabel} className={badgeClassName} role="img" title={badgeLabel}>
      <AppIcon name={badgeIconName} />
    </span>
  );
}

function getTriggerLabel(trigger: string) {
  return TRIGGER_LABELS[trigger] ?? trigger;
}

function getUsageLabel(key: string) {
  const labels: Record<string, string> = {
    llm: "LLM 토큰",
    image_generation: "이미지 생성",
    ocr: "OCR 요청",
    google_api: "외부 API 호출",
  };
  return labels[key] ?? key;
}

function getStageLabel(stage: string | null | undefined) {
  if (!stage) {
    return "-";
  }

  const labels: Record<string, string> = {
    topic_selected: "주제 확정",
    script_review: "스크립트 검토",
    image_generation: "이미지 생성 단계",
    posted: "게시 단계",
  };

  return labels[stage] ?? stage;
}

function getAgentInitials(agentName: string) {
  return agentName.slice(0, 2);
}

function summarizeText(text: string, maxLength = 56) {
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength - 1)}…`;
}

function getWorkspaceStep(workspace: AgentWorkspace) {
  if (workspace.artifacts.length > 0) {
    return 5;
  }
  if (workspace.script) {
    return 4;
  }
  if (workspace.topic) {
    return 3;
  }
  return 2;
}

function getWorkspaceParticipants(workspace: AgentWorkspace): WorkspaceParticipant[] {
  return [
    {
      id: "ops",
      name: "YO",
      title: "Team Lead",
      status: getStatusLabel(workspace.status),
      note:
        workspace.artifacts.length > 0
          ? `${workspace.run_id} 실행 산출물을 회수해 공유 중입니다.`
          : `${workspace.week_key} 회의를 열고 전체 흐름을 조율하고 있습니다.`,
      badge: "OPS",
      tone: "ops",
      avatarCode: "YO",
      avatarAccent: "#3776ab",
      avatarSrc: "/avatars/ops-manager-cat.png",
      avatarPosition: "center 34%",
      avatarScale: 1,
      connectionState: "connected",
      connectionLabel: "연결 완료",
    },
    {
      id: "topic",
      name: "MJ",
      title: "Planner",
      status: workspace.topic
        ? "주제 확정"
        : workspace.recommendations.length > 0
          ? `${workspace.recommendations.length}개 후보 준비`
          : "브리프 대기",
      note: workspace.topic
        ? `선정 주제 기준으로 중복 위험과 방향성을 정리했습니다.`
        : workspace.recommendations.length > 0
          ? "추천 후보를 골라주시면 바로 다음 단계로 넘길 수 있습니다."
          : "주제가 정해지면 추천 또는 직접 입력 흐름으로 이어집니다.",
      badge: "TOP",
      tone: "topic",
      avatarCode: "MJ",
      avatarAccent: "#d8a106",
      avatarSrc: "/avatars/deputy-analyst-cat.png",
      avatarPosition: "center 32%",
      avatarScale: 1,
      connectionState: "connected",
      connectionLabel: "대기 중",
    },
    {
      id: "writer",
      name: "DY",
      title: "Writer",
      status: workspace.script ? `스크립트 v${workspace.script.version}` : workspace.topic ? "초안 준비 중" : "대기 중",
      note: workspace.script
        ? `${workspace.script.panels.length}컷 구성을 정리했고 캡션 초안도 함께 올렸습니다.`
        : workspace.topic
          ? "확정된 주제를 받아 4컷 흐름을 쓰고 있습니다."
          : "주제가 확정되면 바로 콘티 초안을 만들겠습니다.",
      badge: "WR",
      tone: "writer",
      avatarCode: "DY",
      avatarAccent: "#2d7a58",
      avatarSrc: "/avatars/writer-artist-cat.png",
      avatarPosition: "center 26%",
      avatarScale: 1,
      connectionState: "connected",
      connectionLabel: workspace.script ? "초안 준비" : "연결 완료",
    },
    {
      id: "qa",
      name: "SY",
      title: "Editor",
      status: workspace.artifacts.length > 0 ? `${workspace.artifacts.length}개 결과물 준비` : workspace.script ? "검수 대기" : "대기 중",
      note:
        workspace.artifacts.length > 0
          ? "최종 파일과 버전 기록을 정리해 전달할 준비가 됐습니다."
          : workspace.script
            ? "스크립트 승인 이후 최종 결과물 품질을 점검합니다."
            : "스크립트가 확정되면 교정과 결과물 정리에 들어갑니다.",
      badge: "QA",
      tone: "qa",
      avatarCode: "SY",
      avatarAccent: "#8b63c7",
      avatarSrc: "/avatars/editor-cat.png",
      avatarPosition: "center 34%",
      avatarScale: 1,
      connectionState: "connected",
      connectionLabel: workspace.artifacts.length > 0 ? "파일 점검 중" : "연결 완료",
    },
  ];
}

function getMeetingPreviewParticipants(isConnecting: boolean): WorkspaceParticipant[] {
  return [
    {
      id: "ops",
      name: "YO",
      title: "Team Lead",
      status: isConnecting ? "회의실 연결 중" : "입장 대기",
      note: isConnecting
        ? "회의실 세션을 열고 직원 에이전트들을 호출하고 있습니다."
        : "회의실 입장 버튼을 누르면 가장 먼저 입장합니다.",
      badge: "OPS",
      tone: "ops",
      avatarCode: "YO",
      avatarAccent: "#3776ab",
      avatarSrc: "/avatars/ops-manager-cat.png",
      avatarPosition: "center 34%",
      avatarScale: 1,
      connectionState: isConnecting ? "connecting" : "offline",
      connectionLabel: isConnecting ? "연결 시도 중" : "오프라인",
    },
    {
      id: "topic",
      name: "MJ",
      title: "Planner",
      status: isConnecting ? "호출 중" : "입장 대기",
      note: isConnecting ? "주제 추천 및 중복 검사를 준비 중입니다." : "회의실 입장 후 브리프를 기다립니다.",
      badge: "TOP",
      tone: "topic",
      avatarCode: "MJ",
      avatarAccent: "#d8a106",
      avatarSrc: "/avatars/deputy-analyst-cat.png",
      avatarPosition: "center 32%",
      avatarScale: 1,
      connectionState: isConnecting ? "connecting" : "offline",
      connectionLabel: isConnecting ? "호출 중" : "오프라인",
    },
    {
      id: "writer",
      name: "DY",
      title: "Writer",
      status: isConnecting ? "호출 중" : "입장 대기",
      note: isConnecting ? "스크립트 보드와 캡션 초안을 세팅 중입니다." : "주제가 오면 바로 초안을 작성합니다.",
      badge: "WR",
      tone: "writer",
      avatarCode: "DY",
      avatarAccent: "#2d7a58",
      avatarSrc: "/avatars/writer-artist-cat.png",
      avatarPosition: "center 26%",
      avatarScale: 1,
      connectionState: isConnecting ? "connecting" : "offline",
      connectionLabel: isConnecting ? "호출 중" : "오프라인",
    },
    {
      id: "qa",
      name: "SY",
      title: "Editor",
      status: isConnecting ? "호출 중" : "입장 대기",
      note: isConnecting ? "교정 및 결과물 확인 보드를 준비 중입니다." : "후반 검수 단계에서 합류합니다.",
      badge: "QA",
      tone: "qa",
      avatarCode: "SY",
      avatarAccent: "#8b63c7",
      avatarSrc: "/avatars/editor-cat.png",
      avatarPosition: "center 34%",
      avatarScale: 1,
      connectionState: isConnecting ? "connecting" : "offline",
      connectionLabel: isConnecting ? "호출 중" : "오프라인",
    },
  ];
}

function getWorkspacePhases(workspace: AgentWorkspace): WorkspacePhase[] {
  const currentStep = getWorkspaceStep(workspace);

  return WORKSPACE_PHASE_TEMPLATES.map((phase, index) => {
    const step = index + 1;
    let state: WorkspacePhase["state"] = "upcoming";
    if (step < currentStep) {
      state = "done";
    } else if (step === currentStep) {
      state = "active";
    }

    return { ...phase, state };
  });
}

function getSpeakerForStage(stage: string): WorkspaceParticipant["id"] {
  const normalizedStage = stage.toLowerCase();

  if (normalizedStage.includes("topic") || normalizedStage.includes("recommend")) {
    return "topic";
  }
  if (normalizedStage.includes("script") || normalizedStage.includes("panel")) {
    return "writer";
  }
  if (
    normalizedStage.includes("artifact") ||
    normalizedStage.includes("image") ||
    normalizedStage.includes("final") ||
    normalizedStage.includes("correct")
  ) {
    return "qa";
  }
  return "ops";
}

function getActiveParticipantId(workspace: AgentWorkspace): WorkspaceParticipant["id"] {
  if (workspace.artifacts.length > 0) {
    return "qa";
  }
  if (workspace.script) {
    return "writer";
  }
  if (workspace.topic || workspace.recommendations.length > 0) {
    return "topic";
  }
  return "ops";
}

function getMeetingPreviewConversation(isConnecting: boolean): WorkspaceConversationItem[] {
  if (!isConnecting) {
    return [
      {
        id: "preview-ops",
        speakerId: "ops",
        speakerName: "YO",
        speakerTitle: "Team Lead",
        tone: "system",
        message: "회의실 입장 버튼을 누르면 직원 에이전트들을 순차적으로 호출하고 연결 상태를 확인합니다.",
        meta: "대기 상태",
      },
    ];
  }

  return [
    {
      id: "connecting-ops",
      speakerId: "ops",
      speakerName: "YO",
      speakerTitle: "Team Lead",
      tone: "system",
      message: "회의실을 개설했습니다. 직원 에이전트 연결 상태를 확인하는 중입니다.",
      meta: "회의실 개설",
    },
    {
      id: "connecting-topic",
      speakerId: "topic",
      speakerName: "MJ",
      speakerTitle: "Planner",
      tone: "staff",
      message: "브리프 수신 대기 중입니다. 연결이 완료되면 바로 주제 후보를 검토하겠습니다.",
      meta: "호출 응답",
    },
  ];
}

function getWorkspaceConversation(workspace: AgentWorkspace): WorkspaceConversationItem[] {
  const participants = getWorkspaceParticipants(workspace);
  const participantMap = new Map(participants.map((participant) => [participant.id, participant]));
  const conversation: WorkspaceConversationItem[] = [];

  function pushMessage(
    id: string,
    speakerId: WorkspaceParticipant["id"],
    message: string,
    meta: string,
    options?: { tone?: WorkspaceConversationItem["tone"] },
  ) {
    const speaker = participantMap.get(speakerId);
    if (!speaker) {
      return;
    }

    conversation.push({
      id,
      speakerId,
      speakerName: speaker.name,
      speakerTitle: speaker.title,
      tone: options?.tone ?? "staff",
      message,
      meta,
    });
  }

  pushMessage(
    "ops-kickoff",
    "ops",
    `${workspace.week_key} 회의를 열었습니다. 현재 실행 ID는 ${workspace.run_id}이고 상태는 ${getStatusLabel(workspace.status)}입니다.`,
    workspace.started_at,
    { tone: "system" },
  );

  if (!workspace.topic) {
    pushMessage(
      "topic-request",
      "topic",
      workspace.recommendations.length > 0
        ? `사장님 요청 기준으로 후보를 추렸습니다.\n${workspace.recommendations.map((topic, index) => `${index + 1}. ${topic}`).join("\n")}`
        : "지시가 들어오는 즉시 중복 위험을 체크하고 추천 후보를 준비하겠습니다.",
      workspace.recommendations.length > 0 ? "추천안 공유" : "편성 대기",
    );
  }

  if (workspace.topic) {
    pushMessage(
      "topic-selected",
      "topic",
      `지시받은 "${workspace.topic}" 안건을 확정했습니다. 유사 주제 충돌 없이 진행 가능한 방향으로 정리했습니다.`,
      "편성 확정",
    );
  }

  if (workspace.script) {
    pushMessage(
      "writer-script",
      "writer",
      `스크립트 v${workspace.script.version} 초안을 올립니다.\n제목: ${workspace.script.title}\n캡션: ${workspace.script.caption}`,
      "작가 초안",
    );
  }

  if (workspace.artifacts.length > 0) {
    pushMessage(
      "qa-output",
      "qa",
      `최종 결과물 ${workspace.artifacts.length}개를 정리했습니다. 승인 또는 후속 게시 단계로 넘길 수 있습니다.`,
      "에디터 전달",
      { tone: "success" },
    );
  }

  workspace.logs.slice(-4).forEach((log, index) => {
    pushMessage(
      `log-${index}-${log.timestamp}`,
      getSpeakerForStage(log.stage),
      log.message,
      `${log.stage} · ${log.timestamp}`,
      {
        tone:
          log.status === "approved" || log.status === "done" || log.status === "posted"
            ? "success"
            : log.status === "failed"
              ? "system"
              : "staff",
      },
    );
  });

  return conversation;
}

function WorkspaceParticipantDesk({
  participant,
  isActive,
  index,
}: {
  participant: WorkspaceParticipant;
  isActive: boolean;
  index: number;
}) {
  return (
    <article
      className={`workspace-desk-seat tone-${participant.tone} ${isActive ? "is-active" : ""}`}
      style={
        {
          "--avatar-accent": participant.avatarAccent,
          "--tile-delay": `${index * 90}ms`,
        } as CSSProperties
      }
    >
      <div className="workspace-desk-head">
        <span className="workspace-person-badge">{participant.badge}</span>
        <span className={`workspace-connection-pill is-${participant.connectionState}`}>
          <i />
          {participant.connectionLabel}
        </span>
      </div>
      <div className="workspace-desk-chair">
        <div className="workspace-person-avatar">
          <div className="workspace-person-avatar-ring">
            <img
              alt={participant.name}
              className="workspace-person-avatar-image"
              src={participant.avatarSrc}
              style={{
                objectPosition: participant.avatarPosition,
                transform: `scale(${participant.avatarScale})`,
              }}
            />
          </div>
        </div>
      </div>
      <div className="workspace-person-copy">
        <h3>{participant.name}</h3>
        <div className="workspace-person-title">{participant.title}</div>
        <div className="workspace-person-status">{participant.status}</div>
      </div>
    </article>
  );
}

function WorkspaceConversationBubble({
  item,
  participant,
  index,
}: {
  item: WorkspaceConversationItem;
  participant: WorkspaceParticipant;
  index: number;
}) {
  return (
    <article
      className={`workspace-message tone-${participant.tone}`}
      style={{ "--message-delay": `${index * 80}ms`, "--avatar-accent": participant.avatarAccent } as CSSProperties}
    >
      <div className="workspace-message-avatar">
        <div className="workspace-message-avatar-ring">
          <img
            alt={participant.name}
            className="workspace-message-avatar-image"
            src={participant.avatarSrc}
            style={{
              objectPosition: participant.avatarPosition,
              transform: `scale(${participant.avatarScale})`,
            }}
          />
        </div>
      </div>
      <div className={`workspace-message-bubble tone-${item.tone}`}>
        <div className="workspace-message-head">
          <strong>{item.speakerName}</strong>
          <span>{item.speakerTitle}</span>
          <span>{item.meta}</span>
        </div>
        <p>{item.message}</p>
      </div>
    </article>
  );
}

function BackButton({ to, label }: { to: string; label: string }) {
  const navigate = useNavigate();

  return (
    <div className="page-back-row">
      <button
        className="back-button"
        type="button"
        onClick={() => {
          if (window.history.length > 1) {
            navigate(-1);
            return;
          }
          navigate(to);
        }}
      >
        ← {label}
      </button>
    </div>
  );
}

function WorkspaceActionButton({
  children,
  disabled,
  onClick,
  tone = "primary",
}: {
  children: string;
  disabled?: boolean;
  onClick: () => void;
  tone?: "primary" | "secondary";
}) {
  return (
    <button
      className={`button ${tone === "secondary" ? "button-secondary" : ""}`}
      disabled={disabled}
      type="button"
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function LoadingCard() {
  return <section className="card">데이터를 불러오는 중입니다.</section>;
}

function ErrorCard({ message }: { message: string }) {
  return <section className="card error-card">데이터를 불러오지 못했습니다. {message}</section>;
}

function HeaderMeta() {
  const location = useLocation();

  if (location.pathname.startsWith("/agents/") && location.pathname.endsWith("/runs")) {
    return {
      title: "Run History",
      description: "실행 이력과 상태 변화를 빠르게 검토합니다.",
      activeNav: "agents",
    };
  }

  if (location.pathname.startsWith("/agents/")) {
    return {
      title: "Agent Overview",
      description: "선택한 에이전트의 실행과 결과를 관리합니다.",
      activeNav: "agents",
    };
  }

  if (location.pathname.startsWith("/runs/")) {
    return {
      title: "Run Summary",
      description: "단일 실행의 로그와 산출물을 확인합니다.",
      activeNav: "agents",
    };
  }

  if (location.pathname === "/agents") {
    return {
      title: "Agents",
      description: "등록된 자동화 에이전트 목록을 확인합니다.",
      activeNav: "agents",
    };
  }

  if (location.pathname === "/costs") {
    return {
      title: "Costs",
      description: "에이전트별 비용과 사용량을 추적합니다.",
      activeNav: "costs",
    };
  }

  if (location.pathname === "/settings") {
    return {
      title: "Settings",
      description: "플랫폼 운영에 필요한 기본 설정을 관리합니다.",
      activeNav: "settings",
    };
  }

  return {
    title: "Dashboard",
    description: "전체 운영 현황을 한 화면에서 확인합니다.",
    activeNav: "dashboard",
  };
}

function AppShell() {
  const health = useAsyncData(fetchHealth, []);
  const meta = HeaderMeta();
  const handleRefresh = () => {
    window.location.reload();
  };

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="sidebar-top">
          <div className="brand">
            <div className="brand-mark">AP</div>
            <div>
              <div className="brand-title">Agent Platform</div>
              <div className="brand-subtitle">Multi-Agent Console</div>
            </div>
          </div>
          <div className="nav-section-title">Main</div>
          <nav className="nav">
            <NavLink className={({ isActive }) => `nav-link ${isActive ? "is-active" : ""}`} end to="/">
              <span className="nav-icon">
                <AppIcon name="dashboard" />
              </span>
              <span>Dashboard</span>
            </NavLink>
            <NavLink className={({ isActive }) => `nav-link ${isActive ? "is-active" : ""}`} to="/agents">
              <span className="nav-icon">
                <AppIcon name="agents" />
              </span>
              <span>Agents</span>
            </NavLink>
            <NavLink className={({ isActive }) => `nav-link ${isActive ? "is-active" : ""}`} to="/costs">
              <span className="nav-icon">
                <AppIcon name="costs" />
              </span>
              <span>Costs</span>
            </NavLink>
            <NavLink className={({ isActive }) => `nav-link ${isActive ? "is-active" : ""}`} to="/settings">
              <span className="nav-icon">
                <AppIcon name="settings" />
              </span>
              <span>Settings</span>
            </NavLink>
          </nav>
        </div>
        <div className="sidebar-footer">
          <Link className="button sidebar-button" to="/agents">
            + New Agent
          </Link>
          <div className="support-card">
            <div className="support-title">Quick Note</div>
            <div className="support-text">
              새 자동화 에이전트는 Agents 화면에서 등록하고 상세 페이지에서 실행합니다.
            </div>
            <div className="support-health">API: {health.data?.status ?? (health.loading ? "checking" : "offline")}</div>
          </div>
        </div>
      </aside>
      <main className="content">
        <header className="page-header">
          <div className="page-title-block">
            <h1>{meta.title}</h1>
            <p className="muted">{meta.description}</p>
          </div>
          <div className="header-actions">
            <button className="icon-button" aria-label="페이지 새로고침" onClick={handleRefresh} type="button">
              <AppIcon name="refresh" />
            </button>
          </div>
        </header>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/agents" element={<AgentsPage />} />
          <Route path="/agents/:agentSlug" element={<AgentDetailPage />} />
          <Route path="/agents/:agentSlug/runs" element={<AgentRunsPage />} />
          <Route path="/runs/:runId" element={<RunDetailPage />} />
          <Route path="/costs" element={<CostsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  );
}

function DashboardPage() {
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
        <div className="run-detail-overlay" onClick={() => setSelectedRunId(null)} role="presentation">
          <aside className="run-detail-drawer" onClick={(event) => event.stopPropagation()}>
            <div className="run-detail-drawer-head">
              <div>
                <div className="section-kicker">Run Detail</div>
                <h3>{selectedRunId}</h3>
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

function AgentsPage() {
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

function AgentDetailPage() {
  const { agentSlug = "" } = useParams();
  const [refreshKey, setRefreshKey] = useState(0);
  const [topicInput, setTopicInput] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [agentPanelExpanded, setAgentPanelExpanded] = useState(true);
  const [historyExpanded, setHistoryExpanded] = useState(false);
  const [workspaceMetaExpanded, setWorkspaceMetaExpanded] = useState(false);
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

  const { agent, latest_run: latestRun, cost_total: costTotal, artifacts } = state.data;
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

  async function handleTopicSubmit(event: FormEvent<HTMLFormElement>) {
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
        <button className="section-toggle" type="button" onClick={() => setAgentPanelExpanded((value) => !value)}>
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
        <button className="section-toggle" type="button" onClick={() => setHistoryExpanded((value) => !value)}>
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

function AgentRunsPage() {
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

function RunDetailPage() {
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

function CostsPage() {
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

function SettingsPage() {
  const state = useAsyncData(fetchSettings, []);

  if (state.loading) {
    return <LoadingCard />;
  }

  if (state.error || !state.data) {
    return <ErrorCard message={state.error ?? "데이터가 없습니다."} />;
  }

  return (
    <>
      <section className="two-column">
        <article className="card">
          <h2>Platform Settings</h2>
          <ul className="settings-list">
            {Object.entries(state.data.settings).map(([key, value]) => (
              <li key={key}>
                <span>{key}</span>
                <strong>{value}</strong>
              </li>
            ))}
          </ul>
        </article>
        <article className="card">
          <h2>Next Steps</h2>
          <ul className="check-list">
            <li>OpenAI API Key 연결</li>
            <li>Image Provider 설정</li>
            <li>Google API 인증 정보 설정</li>
            <li>Instagram API 인증 정보 설정</li>
          </ul>
        </article>
      </section>
    </>
  );
}

function ArtifactItem({ artifact }: { artifact: Artifact }) {
  return (
    <li>
      <div>
        <strong>{artifact.artifact_name}</strong>
        <div className="muted">
          {artifact.artifact_type} v{artifact.version}
        </div>
      </div>
      <a className="artifact-link" href={artifact.file_url}>
        Open
      </a>
    </li>
  );
}

function App() {
  return (
    <BrowserRouter>
      <AppShell />
    </BrowserRouter>
  );
}

export default App;
