"use client";

import { useCallback, useEffect, useState } from "react";

export type ThemeChoice = "dark" | "light" | "system";
const KEY = "aci-theme";

function apply(choice: ThemeChoice) {
  const dark =
    choice === "dark" ||
    (choice === "system" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

function read(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "system" ? v : "dark";
  } catch {
    return "dark";
  }
}

/** Theme preference (dark default), stored per browser. The first paint is handled by a script in the root layout. */
export function useTheme(): [ThemeChoice, (t: ThemeChoice) => void] {
  const [theme, setThemeState] = useState<ThemeChoice>("dark");

  useEffect(() => {
    const t = read();
    void Promise.resolve().then(() => setThemeState(t));
    if (t !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => apply("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const setTheme = useCallback((t: ThemeChoice) => {
    try {
      localStorage.setItem(KEY, t);
    } catch {
      /* private mode: theme applies for this page only */
    }
    apply(t);
    setThemeState(t);
  }, []);

  return [theme, setTheme];
}
