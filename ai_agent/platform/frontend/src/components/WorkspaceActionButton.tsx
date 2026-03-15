import React from "react";

export function WorkspaceActionButton({
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
