import React from "react";
import { fetchSettings } from "../lib/api";
import { useAsyncData } from "../hooks/useAsyncData";
import { LoadingCard } from "../components/LoadingCard";
import { ErrorCard } from "../components/ErrorCard";

export function SettingsPage() {
  const state = useAsyncData(fetchSettings, []);

  if (state.loading) {
    return <LoadingCard />;
  }

  if (state.error || !state.data) {
    return <ErrorCard message={state.error ?? "데이터가 없습니다."} />;
  }

  return (
    <>
      <section className="two-column">
        <article className="card">
          <h2>Platform Settings</h2>
          <ul className="settings-list">
            {Object.entries(state.data.settings).map(([key, value]) => (
              <li key={key}>
                <span>{key}</span>
                <strong>{value}</strong>
              </li>
            ))}
          </ul>
        </article>
        <article className="card">
          <h2>Next Steps</h2>
          <ul className="check-list">
            <li>OpenAI API Key 연결</li>
            <li>Image Provider 설정</li>
            <li>Google API 인증 정보 설정</li>
            <li>Instagram API 인증 정보 설정</li>
          </ul>
        </article>
      </section>
    </>
  );
}
