import React from "react";

export function LoadingCard() {
  return (
    <section aria-busy="true" aria-label="데이터 로딩 중" className="card loading-card">
      <div className="skeleton skeleton-title" />
      <div className="skeleton skeleton-line" />
      <div className="skeleton skeleton-line skeleton-line-short" />
    </section>
  );
}
