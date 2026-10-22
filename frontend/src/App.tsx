import { useEffect, useState } from "react";
import LiveSim from "./pages/LiveSim";
import Research from "./pages/Research";
import Sessions from "./pages/Sessions";

const PAGES = {
  sim: { label: "Live sim", el: <LiveSim /> },
  research: { label: "Research", el: <Research /> },
  sessions: { label: "Sessions & replay", el: <Sessions /> },
} as const;
type PageKey = keyof typeof PAGES;

const fromHash = (): PageKey => {
  const h = location.hash.slice(1);
  return h in PAGES ? (h as PageKey) : "sim";
};

export default function App() {
  const [page, setPage] = useState<PageKey>(fromHash);
  useEffect(() => {
    const on = () => setPage(fromHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return (
    <>
      <header>
        <div className="brand">LOB<span>-X</span> <small>market-making research platform</small></div>
        <nav>
          {(Object.keys(PAGES) as PageKey[]).map((k) => (
            <a key={k} href={`#${k}`} className={k === page ? "active" : ""}>{PAGES[k].label}</a>
          ))}
        </nav>
      </header>
      <main>{PAGES[page].el}</main>
      <footer className="dim small">Paper trading with the backtest simulator · no real orders · calibrated GLFT / Avellaneda–Stoikov · Hawkes order flow · queue-aware fills</footer>
    </>
  );
}
