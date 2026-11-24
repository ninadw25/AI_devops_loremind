import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate, Link } from "react-router-dom";
import { Loader2, AlertCircle, ArrowLeft } from "lucide-react";
import { useAuth, roleHome } from "@/auth/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface LocationState {
  from?: { pathname?: string };
}

export default function Login() {
  const { login, isAuthenticated, identity } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Already signed in → straight to the role home.
  if (isAuthenticated) {
    return <Navigate to={roleHome(identity)} replace />;
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (loading) return;
    setError(null);
    setLoading(true);
    try {
      const id = await login(email.trim(), password);
      const from = (location.state as LocationState | null)?.from?.pathname;
      const dest = from && from !== "/login" ? from : roleHome(id);
      navigate(dest, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-[#f8f9fb] flex flex-col items-center justify-center px-4">
      <Link
        to="/"
        className="absolute top-6 left-6 flex items-center gap-1.5 text-[13px] font-medium text-slate-500 hover:text-slate-800 transition-colors"
      >
        <ArrowLeft className="w-4 h-4" strokeWidth={2} />
        Back to home
      </Link>

      <div className="w-full max-w-[400px]">
        {/* Brand mark */}
        <div className="flex flex-col items-center mb-7">
          <img src="/images/logo.png" alt="Loremind" className="h-10 w-auto object-contain mb-5" />
          <h1 className="text-[22px] font-semibold text-slate-900 leading-tight">Sign in to Loremind</h1>
          <p className="text-[13px] text-slate-500 mt-1">Access your organization's knowledge dashboard</p>
        </div>

        {/* Card */}
        <form
          onSubmit={onSubmit}
          className="bg-white border border-slate-200/70 rounded-xl shadow-sm p-7 space-y-5"
        >
          {error && (
            <div className="flex items-start gap-2.5 rounded-lg border border-rose-200 bg-rose-50 px-3.5 py-2.5">
              <AlertCircle className="w-4 h-4 text-rose-500 mt-0.5 shrink-0" strokeWidth={2} />
              <span className="text-[12.5px] text-rose-700 leading-snug">{error}</span>
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="email" className="text-[13px] text-slate-700">Email</Label>
            <Input
              id="email"
              type="email"
              autoComplete="username"
              placeholder="you@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              disabled={loading}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="password" className="text-[13px] text-slate-700">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              disabled={loading}
            />
          </div>

          <Button type="submit" className="w-full" disabled={loading || !email || !password}>
            {loading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" /> Signing in…
              </>
            ) : (
              "Sign in"
            )}
          </Button>
        </form>

        <p className="text-center text-[11.5px] text-slate-400 mt-6">
          Your role determines your dashboard - organization admins and Loremind
          administrators are routed automatically.
        </p>
      </div>
    </div>
  );
}
