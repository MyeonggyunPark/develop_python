import React from "react";
import { type Theme, useTheme } from "../hooks/useTheme";

export function ThemeToggle() {
  const [theme, setTheme] = useTheme();

  const options: { value: Theme; label: string }[] = [
    { value: "light", label: "라이트" },
    { value: "dark", label: "다크" },
    { value: "system", label: "시스템" },
  ];

  return (
    <div aria-label="테마 선택" className="theme-toggle" role="group">
      {options.map((opt) => (
        <button
          aria-pressed={theme === opt.value}
          className={`theme-toggle-option ${theme === opt.value ? "is-active" : ""}`}
          key={opt.value}
          type="button"
          onClick={() => setTheme(opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
