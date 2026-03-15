import React from "react";
import { Link, useMatch, useResolvedPath } from "react-router-dom";
import { AppIcon, type IconName } from "./AppIcon";

export function AppNavLink({ to, end, icon, label }: { to: string; end?: boolean; icon: IconName; label: string }) {
  const resolved = useResolvedPath(to);
  const match = useMatch({ path: resolved.pathname, end: end ?? false });
  const isActive = match !== null;

  return (
    <Link
      aria-current={isActive ? "page" : undefined}
      className={`nav-link ${isActive ? "is-active" : ""}`}
      to={to}
    >
      <span aria-hidden="true" className="nav-icon">
        <AppIcon name={icon} />
      </span>
      <span>{label}</span>
    </Link>
  );
}
