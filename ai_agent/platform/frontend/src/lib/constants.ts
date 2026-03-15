import type { IconName } from "../components/AppIcon";

export const STATUS_LABELS: Record<string, string> = {
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

export const TRIGGER_LABELS: Record<string, string> = {
  manual: "Manual",
  scheduled: "Scheduled",
};

export const DASHBOARD_SUMMARY_ICONS: IconName[] = ["activeAgents", "recentRuns", "failedRuns", "recentCost"];

export const WORKSPACE_PHASE_TEMPLATES = [
  { id: "brief", label: "브리프 접수", owner: "사장", detail: "이번 주 방향과 마감 공유" },
  { id: "topic", label: "주제 확정", owner: "편성", detail: "직접 입력 또는 추천 선택" },
  { id: "script", label: "스크립트 작성", owner: "작가", detail: "4컷 흐름과 캡션 초안 작성" },
  { id: "review", label: "교정 및 승인", owner: "에디터", detail: "재생성 여부 확인 후 승인" },
  { id: "output", label: "결과물 전달", owner: "운영", detail: "최종 산출물 정리 및 공유" },
] as const;
