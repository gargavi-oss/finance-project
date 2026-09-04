import { FormEvent, useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import Brand from "../components/Brand";
import { useAuth } from "../contexts/AuthContext";
import { ApiError } from "../lib/api";

export default function AuthPage({ mode }: { mode: "login" | "signup" }) {
  const { user, login, signup } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const isSignup = mode === "signup";

  if (user) return <Navigate to="/app/live" replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (isSignup && name.trim().length < 2) return setError("Enter your full name.");
    if (!email.includes("@")) return setError("Enter a valid work email.");
    if (password.length < (isSignup ? 8 : 1)) return setError("Password must contain at least 8 characters.");
    setBusy(true);
    try {
      if (isSignup) await signup(name.trim(), email.trim(), password);
      else await login(email.trim(), password);
      const destination = (location.state as { from?: string } | null)?.from ?? "/app/live";
      navigate(destination, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Unable to connect. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-layout">
      <section className="auth-story">
        <div className="auth-story-inner">
          <Brand inverted />
          <div className="auth-quote">
            <span className="case-kicker">Evidence should travel with the decision.</span>
            <h1>{isSignup ? "Build a finance review process your team can defend." : "Return to the evidence control room."}</h1>
            <p>{isSignup ? "Create a secure analyst workspace and investigate your first document in minutes." : "Sign in to continue live investigations, review verdicts, and record audit-ready decisions."}</p>
          </div>
          <div className="auth-proof">
            <div><strong>Six agents</strong><span>working as one investigation</span></div>
            <div><strong>Full trace</strong><span>from upload to human action</span></div>
          </div>
        </div>
      </section>

      <main className="auth-form-side">
        <div className="auth-form-wrap reveal-up">
          <Link to="/" className="back-link">← Back to overview</Link>
          <div className="auth-heading">
            <span className="eyebrow"><span /> {isSignup ? "Create analyst workspace" : "Secure access"}</span>
            <h2>{isSignup ? "Start your first investigation." : "Welcome back."}</h2>
            <p>{isSignup ? "No credit card. No setup maze. Just a working evidence pipeline." : "Use your DocForensic analyst credentials to continue."}</p>
          </div>

          <form className="auth-form" onSubmit={submit} noValidate>
            {isSignup && <label>Full name<input autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Avi Garg" /></label>}
            <label>Work email<input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="avi@finance.team" /></label>
            <label>Password<input type="password" autoComplete={isSignup ? "new-password" : "current-password"} value={password} onChange={(e) => setPassword(e.target.value)} placeholder={isSignup ? "At least 8 characters" : "Enter your password"} /></label>
            {error && <div className="form-error" role="alert">{error}</div>}
            <button className="button button-primary button-large auth-submit" disabled={busy}>
              {busy ? "Securing workspace…" : isSignup ? "Create workspace" : "Enter control room"}
              {!busy && <span>→</span>}
            </button>
          </form>

          <p className="auth-switch">
            {isSignup ? "Already have an account?" : "New to DocForensic AI?"}{" "}
            <Link to={isSignup ? "/login" : "/signup"}>{isSignup ? "Sign in" : "Create a workspace"}</Link>
          </p>
        </div>
      </main>
    </div>
  );
}
