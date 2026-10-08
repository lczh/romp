// Session ↔ workspace matching: which romp sessions live in (or above) the
// folders this VS Code window has open. Dirs come from the kernel's /sessions
// endpoint (the authoritative session list); folders from
// vscode.workspace.workspaceFolders. Pure decision core — extension.ts owns
// the fetch and the QuickPick.

export type SessionInfo = { id: string; name: string; dir: string };

export function normalizeDir(p: string): string {
  let s = String(p || "").replace(/\/+$/, "");
  return s || "/";
}

// A session belongs to this window when its dir and a workspace folder
// coincide or one contains the other (a worktree opened directly, a repo root
// containing the session's subdir, or a session running at the repo root
// while the window has a subfolder open).
export function sessionMatchesFolders(dir: string, folders: string[]): boolean {
  const d = normalizeDir(dir);
  if (d === "/") return false;
  return folders.some((f) => {
    const w = normalizeDir(f);
    if (w === "/") return false;
    return d === w || d.startsWith(w + "/") || w.startsWith(d + "/");
  });
}

// What the kernel's GET /sessions answer says (extension.ts owns the fetch): the rows when it answered 200; the kernel's own
// words when it answered that its session list is unavailable (its 503 while a listing build fails there); and no answer
// at all (statusCode null: refused, timed out). Each reads differently: an empty list is "no sessions", a refusal is the
// kernel's message, and only silence asks whether the kernel is running.
export type SessionsAnswer = { sessions: SessionInfo[]; answered: boolean; error: string };

export function sessionsAnswer(statusCode: number | null, body: string): SessionsAnswer {
  if (statusCode === null) return { sessions: [], answered: false, error: "" };
  let j: any = null;
  try { j = JSON.parse(body); } catch { j = null; }
  if (statusCode === 200 && Array.isArray(j)) {
    return { sessions: j.map((s: any) => ({ id: String(s.id), name: String(s.name), dir: String(s.dir || "") })), answered: true, error: "" };
  }
  const why = j && typeof j === "object" && typeof j.error === "string" && j.error ? j.error : `HTTP ${statusCode}`;
  return { sessions: [], answered: true, error: why };
}

// The warning a picker shows when the answer carries no sessions to pick from.
export function sessionsWarning(a: SessionsAnswer): string {
  if (!a.answered) return "romp: the kernel did not answer (is it running? `romp up`).";
  if (a.error) return `romp: the kernel answered, but its session list is unavailable: ${a.error}`;
  return "romp: no sessions.";
}

export function sessionsForWorkspace(sessions: SessionInfo[], folders: string[]): SessionInfo[] {
  return (sessions || []).filter((s) => s && s.dir && sessionMatchesFolders(s.dir, folders));
}

// The citation text for the composer: absolute path (unambiguous for any
// session, whatever its cwd), with a line range only when the selection
// actually spans one — a bare cursor cites the file, not a line.
export function citeText(file: string, startLine?: number, endLine?: number, hasSelection = false): string {
  if (!hasSelection || !startLine) return file;
  if (!endLine || endLine === startLine) return `${file}:${startLine}`;
  return `${file}:${startLine}-${endLine}`;
}
