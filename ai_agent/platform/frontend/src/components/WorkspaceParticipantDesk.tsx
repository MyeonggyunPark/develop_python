import React, { type CSSProperties } from "react";
import type { WorkspaceParticipant } from "../types/workspace";

export function WorkspaceParticipantDesk({
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
