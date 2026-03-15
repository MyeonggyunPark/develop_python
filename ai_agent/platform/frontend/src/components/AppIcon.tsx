import React from "react";

export type IconName =
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

export function AppIcon({ name }: { name: IconName }) {
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
