import { Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import Shell from "./components/Shell";
import { AuthProvider, useAuth } from "./contexts/AuthContext";
import AuditLog from "./pages/AuditLog";
import AuthPage from "./pages/AuthPage";
import LandingPage from "./pages/LandingPage";
import LiveVerification from "./pages/LiveVerification";
import RingDetection from "./pages/RingDetection";
import VerdictReport from "./pages/VerdictReport";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<AuthPage mode="login" />} />
        <Route path="/signup" element={<AuthPage mode="signup" />} />
        <Route element={<ProtectedRoute />}>
          <Route path="/app" element={<Shell><Outlet /></Shell>}>
            <Route index element={<Navigate to="/app/live" replace />} />
            <Route path="live" element={<LiveVerification />} />
            <Route path="verdict" element={<VerdictReport />} />
            <Route path="audit" element={<AuditLog />} />
            <Route path="ring" element={<RingDetection />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}

function ProtectedRoute() {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <div className="app-loading"><div className="loading-mark" /><span>Restoring secure session</span></div>;
  if (!user) return <Navigate to="/login" state={{ from: location.pathname + location.search }} replace />;
  return <Outlet />;
}
