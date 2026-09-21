import { useState, type FormEvent } from "react";
import {
  apiErrorMessage,
  loginUser,
  registerUser,
  storeAuthSession,
  type AuthResponse,
} from "@/lib/api";

interface AuthPanelProps {
  onAuthenticated: (session: AuthResponse) => void;
}

type AuthMode = "login" | "register";

export default function AuthPanel({ onAuthenticated }: AuthPanelProps) {
  const [mode, setMode] = useState<AuthMode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const normalizedUsername = username.trim();
    if (!normalizedUsername || !password) {
      setError("Enter a username and password.");
      return;
    }

    setBusy(true);
    setError(null);
    try {
      const session = mode === "login"
        ? await loginUser(normalizedUsername, password)
        : await registerUser(normalizedUsername, password);
      storeAuthSession(session);
      onAuthenticated(session);
    } catch (requestError) {
      setError(apiErrorMessage(
        requestError,
        mode === "login" ? "Login failed." : "Registration failed.",
      ));
    } finally {
      setBusy(false);
    }
  };

  const switchMode = (nextMode: AuthMode) => {
    setMode(nextMode);
    setPassword("");
    setError(null);
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-[var(--background)] px-4 text-[var(--foreground)]">
      <section className="w-full max-w-md rounded-lg border border-[var(--border)] bg-[var(--card)] p-6 shadow-lg" aria-labelledby="auth-heading">
        <div className="mb-6">
          <p className="text-xs font-semibold uppercase tracking-wider text-amber-500">Capital Preservation First</p>
          <h1 id="auth-heading" className="mt-1 text-2xl font-bold">Market Analysis</h1>
          <p className="mt-2 text-sm text-[var(--muted-foreground)]">
            {mode === "login" ? "Sign in to your private portfolio." : "Create a private portfolio account."}
          </p>
        </div>

        <div className="mb-4 grid grid-cols-2 rounded border border-[var(--border)] p-1" role="tablist" aria-label="Authentication mode">
          {(["login", "register"] as AuthMode[]).map((option) => (
            <button
              key={option}
              type="button"
              role="tab"
              aria-selected={mode === option}
              className={`rounded px-3 py-2 text-sm font-medium ${
                mode === option
                  ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
                  : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
              }`}
              onClick={() => switchMode(option)}
            >
              {option === "login" ? "Sign in" : "Register"}
            </button>
          ))}
        </div>

        <form className="space-y-4" onSubmit={submit}>
          <label className="block text-sm font-medium">
            Username
            <input
              autoComplete="username"
              className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--input)] px-3 py-2"
              disabled={busy}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          </label>
          <label className="block text-sm font-medium">
            Password
            <input
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--input)] px-3 py-2"
              disabled={busy}
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>

          {error && (
            <div className="rounded border border-red-500/50 bg-red-500/10 px-3 py-2 text-sm text-red-300" role="alert">
              {error}
            </div>
          )}

          <button
            className="w-full rounded bg-[var(--primary)] px-4 py-2 font-semibold text-[var(--primary-foreground)] disabled:cursor-not-allowed disabled:opacity-50"
            disabled={busy}
            type="submit"
          >
            {busy ? "Please wait..." : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>

        <p className="mt-5 text-xs text-[var(--muted-foreground)]">
          Your session token is kept in this browser tab and cleared when the tab closes or you sign out.
        </p>
      </section>
    </main>
  );
}
