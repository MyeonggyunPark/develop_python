import React from "react";
import { useNavigate } from "react-router-dom";

export function BackButton({ to, label }: { to: string; label: string }) {
  const navigate = useNavigate();

  return (
    <div className="page-back-row">
      <button
        className="back-button"
        type="button"
        onClick={() => {
          if (window.history.length > 1) {
            navigate(-1);
            return;
          }
          navigate(to);
        }}
      >
        ← {label}
      </button>
    </div>
  );
}
