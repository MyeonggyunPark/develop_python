import type { AgentWorkspace } from "../types";
import type { WorkspaceConversationItem, WorkspaceParticipant, WorkspacePhase } from "../types/workspace";
import { WORKSPACE_PHASE_TEMPLATES } from "./constants";
import { getStatusLabel } from "../components/StatusBadge";

export function getWorkspaceStep(workspace: AgentWorkspace) {
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

export function getWorkspaceParticipants(workspace: AgentWorkspace): WorkspaceParticipant[] {
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
          ? "확정된 주제를 받아 6컷 흐름을 쓰고 있습니다."
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

export function getMeetingPreviewParticipants(isConnecting: boolean): WorkspaceParticipant[] {
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

export function getWorkspacePhases(workspace: AgentWorkspace): WorkspacePhase[] {
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

export function getSpeakerForStage(stage: string): WorkspaceParticipant["id"] {
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

export function getActiveParticipantId(workspace: AgentWorkspace): WorkspaceParticipant["id"] {
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

export function getMeetingPreviewConversation(isConnecting: boolean): WorkspaceConversationItem[] {
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

export function getWorkspaceConversation(workspace: AgentWorkspace): WorkspaceConversationItem[] {
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
