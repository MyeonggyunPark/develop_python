import React from "react";
import { Link, Route, Routes, useLocation } from "react-router-dom";
import { fetchHealth } from "../lib/api";
import { useAsyncData } from "../hooks/useAsyncData";
import { AppIcon } from "./AppIcon";
import { AppNavLink } from "./AppNavLink";
import { ThemeToggle } from "./ThemeToggle";
import { DashboardPage } from "../pages/DashboardPage";
import { AgentsPage } from "../pages/AgentsPage";
import { AgentDetailPage } from "../pages/AgentDetailPage";
import { AgentRunsPage } from "../pages/AgentRunsPage";
import { RunDetailPage } from "../pages/RunDetailPage";
import { CostsPage } from "../pages/CostsPage";
import { SettingsPage } from "../pages/SettingsPage";

function HeaderMeta() {
  const location = useLocation();

  if (location.pathname.startsWith("/agents/") && location.pathname.endsWith("/runs")) {
    return {
      title: "Run History",
      description: "실행 이력과 상태 변화를 빠르게 검토합니다.",
      activeNav: "agents",
    };
  }

  if (location.pathname.startsWith("/agents/")) {
    return {
      title: "Agent Overview",
      description: "선택한 에이전트의 실행과 결과를 관리합니다.",
      activeNav: "agents",
    };
  }

  if (location.pathname.startsWith("/runs/")) {
    return {
      title: "Run Summary",
      description: "단일 실행의 로그와 산출물을 확인합니다.",
      activeNav: "agents",
    };
  }

  if (location.pathname === "/agents") {
    return {
      title: "Agents",
      description: "등록된 자동화 에이전트 목록을 확인합니다.",
      activeNav: "agents",
    };
  }

  if (location.pathname === "/costs") {
    return {
      title: "Costs",
      description: "에이전트별 비용과 사용량을 추적합니다.",
      activeNav: "costs",
    };
  }

  if (location.pathname === "/settings") {
    return {
      title: "Settings",
      description: "플랫폼 운영에 필요한 기본 설정을 관리합니다.",
      activeNav: "settings",
    };
  }

  return {
    title: "Dashboard",
    description: "전체 운영 현황을 한 화면에서 확인합니다.",
    activeNav: "dashboard",
  };
}

export function AppShell() {
  const health = useAsyncData(fetchHealth, []);
  const meta = HeaderMeta();
  const handleRefresh = () => {
    window.location.reload();
  };

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="sidebar-top">
          <div className="brand">
            <div className="brand-mark">AP</div>
            <div>
              <div className="brand-title">Agent Platform</div>
              <div className="brand-subtitle">Multi-Agent Console</div>
            </div>
          </div>
          <div className="nav-section-title">Main</div>
          <nav aria-label="주 메뉴" className="nav">
            <AppNavLink end icon="dashboard" label="Dashboard" to="/" />
            <AppNavLink icon="agents" label="Agents" to="/agents" />
            <AppNavLink icon="costs" label="Costs" to="/costs" />
            <AppNavLink icon="settings" label="Settings" to="/settings" />
          </nav>
        </div>
        <div className="sidebar-footer">
          <ThemeToggle />
          <Link className="button sidebar-button" to="/agents">
            + New Agent
          </Link>
          <div className="support-card">
            <div className="support-title">Quick Note</div>
            <div className="support-text">
              새 자동화 에이전트는 Agents 화면에서 등록하고 상세 페이지에서 실행합니다.
            </div>
            <div className="support-health">API: {health.data?.status ?? (health.loading ? "checking" : "offline")}</div>
          </div>
        </div>
      </aside>
      <main className="content">
        <header className="page-header">
          <div className="page-title-block">
            <h1>{meta.title}</h1>
            <p className="muted">{meta.description}</p>
          </div>
          <div className="header-actions">
            <button className="icon-button" aria-label="페이지 새로고침" onClick={handleRefresh} type="button">
              <AppIcon name="refresh" />
            </button>
          </div>
        </header>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/agents" element={<AgentsPage />} />
          <Route path="/agents/:agentSlug" element={<AgentDetailPage />} />
          <Route path="/agents/:agentSlug/runs" element={<AgentRunsPage />} />
          <Route path="/runs/:runId" element={<RunDetailPage />} />
          <Route path="/costs" element={<CostsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  );
}
