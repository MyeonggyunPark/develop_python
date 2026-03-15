export type WorkspaceParticipant = {
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

export type WorkspaceConversationItem = {
  id: string;
  speakerId: WorkspaceParticipant["id"];
  speakerName: string;
  speakerTitle: string;
  tone: "staff" | "system" | "success";
  message: string;
  meta: string;
};

export type WorkspacePhase = {
  id: string;
  label: string;
  owner: string;
  detail: string;
  state: "done" | "active" | "upcoming";
};
