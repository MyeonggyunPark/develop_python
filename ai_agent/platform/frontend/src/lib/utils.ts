import { TRIGGER_LABELS } from "./constants";

export function formatMoney(value: number) {
  return `$${value.toFixed(2)}`;
}

export function formatDuration(totalDurationMs: number) {
  return `${(totalDurationMs / 60000).toFixed(1)} min`;
}

export function getTriggerLabel(trigger: string) {
  return TRIGGER_LABELS[trigger] ?? trigger;
}

export function getUsageLabel(key: string) {
  const labels: Record<string, string> = {
    llm: "LLM 토큰",
    image_generation: "이미지 생성",
    ocr: "OCR 요청",
    google_api: "외부 API 호출",
  };
  return labels[key] ?? key;
}

export function getStageLabel(stage: string | null | undefined) {
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

export function getAgentInitials(agentName: string) {
  return agentName.slice(0, 2);
}

export function summarizeText(text: string, maxLength = 56) {
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength - 1)}…`;
}
