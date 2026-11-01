import React, { lazy, Suspense } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import "@fontsource/inter/400.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/800.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/700.css";
import "@fontsource/noto-sans-tamil/400.css";
import "@fontsource/noto-sans-tamil/700.css";
import "./styles/app.css";
import { AppProvider } from "./lib/app";
import { Layout } from "./components/Layout";
import { Loading } from "./components/ui";
import CommandMap from "./pages/CommandMap";

const District = lazy(() => import("./pages/District"));
const Run = lazy(() => import("./pages/Run"));
const Sources = lazy(() => import("./pages/Sources"));
const Replay = lazy(() => import("./pages/Replay"));
const Plans = lazy(() => import("./pages/Plans"));
const Public = lazy(() => import("./pages/Public"));
const Audit = lazy(() => import("./pages/Audit"));
const Tracker = lazy(() => import("./pages/Tracker"));

function NotFound() {
  return <div className="page"><div className="card empty">Page not found · <a href="/">Command map</a></div></div>;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppProvider>
      <BrowserRouter>
        <Layout>
          <Suspense fallback={<Loading label="Loading…" />}>
            <Routes>
              <Route path="/" element={<CommandMap />} />
              <Route path="/district/:token" element={<District />} />
              <Route path="/run/:token" element={<Run />} />
              <Route path="/sources" element={<Sources />} />
              <Route path="/replay" element={<Replay />} />
              <Route path="/plans" element={<Plans />} />
              <Route path="/public" element={<Public />} />
              <Route path="/audit" element={<Audit />} />
              <Route path="/tracker" element={<Tracker />} />
              <Route path="*" element={<NotFound />} />
            </Routes>
          </Suspense>
        </Layout>
      </BrowserRouter>
    </AppProvider>
  </React.StrictMode>,
);
