import React from "react";

export function ErrorCard({ message }: { message: string }) {
  return (
    <section aria-live="polite" className="card error-card" role="alert">
      <span aria-hidden="true">⚠</span>
      <span>데이터를 불러오지 못했습니다. {message}</span>
    </section>
  );
}
