import React from "react";
import { AppIcon, type IconName } from "./AppIcon";
import { STATUS_LABELS } from "../lib/constants";

export function getStatusLabel(status: string) {
  return STATUS_LABELS[status] ?? status;
}

export function getStatusBadgeMeta(status: string): { className: string; icon: IconName; label: string } {
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

export function StatusBadge({
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
