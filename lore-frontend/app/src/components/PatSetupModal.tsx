import { useState } from "react";
import { Loader2, KeyRound, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { setEnvVar, runIngestion } from "@/services/envVarsApi";

interface PatSetupModalProps {
  open: boolean;
  onClose: () => void;
  onComplete?: () => void;
}

/**
 * First-login modal: the organization admin enters a GitLab Personal Access
 * Token (plus the project to ingest). We store the PAT encrypted and kick off a
 * background ingestion run. Key entry here is org-admin only.
 */
export default function PatSetupModal({ open, onClose, onComplete }: PatSetupModalProps) {
  const [pat, setPat] = useState("");
  const [repo, setRepo] = useState("");
  const [gitlabId, setGitlabId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  async function connect() {
    setBusy(true);
    setError(null);
    try {
      await setEnvVar("GITLAB_PAT", pat.trim());
      const result = await runIngestion({
        source: "gitlab",
        repo: repo.trim(),
        gitlabId: gitlabId.trim(),
      });
      setDone(result.detail ?? "Connected. Ingestion has been started.");
      onComplete?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not connect the service.");
    } finally {
      setBusy(false);
    }
  }

  const canSubmit = pat.trim() && repo.trim() && gitlabId.trim() && !busy;

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <KeyRound className="w-[18px] h-[18px] text-blue-700" strokeWidth={2} />
            Connect a service
          </DialogTitle>
          <DialogDescription>
            Enter a GitLab Personal Access Token to let Loremind start ingesting your
            organization's knowledge. You can add more keys later in Settings.
          </DialogDescription>
        </DialogHeader>

        {done ? (
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-[13px] text-emerald-700 flex items-start gap-2">
            <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0" strokeWidth={2} />
            <span>{done}</span>
          </div>
        ) : (
          <div className="space-y-4">
            {error && (
              <div className="rounded-lg border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-[12.5px] text-rose-700">
                {error}
              </div>
            )}
            <div className="space-y-2">
              <Label htmlFor="pat" className="text-[13px] text-slate-700">GitLab Personal Access Token</Label>
              <Input id="pat" type="password" placeholder="glpat-…" value={pat} onChange={(e) => setPat(e.target.value)} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label htmlFor="repo" className="text-[13px] text-slate-700">Project path</Label>
                <Input id="repo" placeholder="owner/repo" value={repo} onChange={(e) => setRepo(e.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="gid" className="text-[13px] text-slate-700">Project ID</Label>
                <Input id="gid" inputMode="numeric" placeholder="59760967" value={gitlabId} onChange={(e) => setGitlabId(e.target.value)} />
              </div>
            </div>
            <p className="text-[11.5px] text-slate-400">
              Your token is encrypted at rest. Only an organization admin can enter keys.
            </p>
          </div>
        )}

        <DialogFooter>
          {done ? (
            <Button onClick={onClose}>Done</Button>
          ) : (
            <>
              <Button variant="ghost" onClick={onClose} disabled={busy}>Skip for now</Button>
              <Button onClick={() => void connect()} disabled={!canSubmit}>
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                Connect &amp; start ingestion
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
