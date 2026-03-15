import React, { type CSSProperties } from "react";
import type { WorkspaceConversationItem, WorkspaceParticipant } from "../types/workspace";

export function WorkspaceConversationBubble({
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
