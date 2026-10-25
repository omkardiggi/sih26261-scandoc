import { StrictMode, lazy, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import "@fontsource-variable/inter";
import "./styles.css";
import { AuthProvider, useAuth } from "./auth";
import { Spinner, ToastProvider } from "./ui";
import AppShell from "./components/AppShell";
import Landing from "./pages/public/Landing";
import { ForgotPassword, Login, Signup, TokenPassword } from "./pages/public/Auth";
import Legal from "./pages/public/Legal";

const Dashboard = lazy(() => import("./pages/Dashboard"));
const InstitutionsList = lazy(() => import("./pages/institutions/InstitutionsList"));
const InstitutionDetail = lazy(() => import("./pages/institutions/InstitutionDetail"));
const Verifier = lazy(() => import("./pages/verifier/Verifier"));
const CaseDetail = lazy(() => import("./pages/verifier/CaseDetail"));
const DocumentReview = lazy(() => import("./pages/verifier/DocumentReview"));
const Inspections = lazy(() => import("./pages/inspection/Inspections"));
const InspectionDetail = lazy(() => import("./pages/inspection/InspectionDetail"));
const MediaViewer = lazy(() => import("./pages/inspection/MediaViewer"));
const Reports = lazy(() => import("./pages/reports/Reports"));

function Protected() {
  const { me, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <Spinner label="Checking session" />;
  if (!me || me.mfa_pending) return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname + loc.search)}`} replace />;
  return <AppShell />;
}

function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Signup />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password" element={<TokenPassword purpose="password_reset" />} />
      <Route path="/accept-invite" element={<TokenPassword purpose="invite" />} />
      <Route path="/privacy" element={<Legal page="privacy" />} />
      <Route path="/terms" element={<Legal page="terms" />} />
      <Route path="/contact" element={<Legal page="contact" />} />
      <Route path="/app" element={<Protected />}>
        <Route index element={<Dashboard />} />
        <Route path="institutions" element={<InstitutionsList />} />
        <Route path="institutions/:id" element={<InstitutionDetail />} />
        <Route path="verifier" element={<Verifier />} />
        <Route path="verifier/cases/:id" element={<CaseDetail />} />
        <Route path="verifier/documents/:id" element={<DocumentReview />} />
        <Route path="inspections" element={<Inspections />} />
        <Route path="inspections/:id" element={<InspectionDetail />} />
        <Route path="inspections/media/:id" element={<MediaViewer />} />
        <Route path="reports" element={<Reports />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

// Light theme by default; dark only when the officer switches to it.
try {
  document.documentElement.dataset.theme = localStorage.getItem("ivp-theme") || "light";
} catch {
  document.documentElement.dataset.theme = "light";
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
          <Suspense fallback={<Spinner />}>
            <App />
          </Suspense>
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
