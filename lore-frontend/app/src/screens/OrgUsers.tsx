import { useEffect, useState, useCallback } from "react";
import { Loader2, UserPlus, Trash2, Pause, Play } from "lucide-react";
import AppShell from "@/components/app-shell/AppShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  listUsers,
  inviteUser,
  updateUser,
  removeUser,
  type OrgMember,
} from "@/services/orgUsersApi";

const ROLES = ["owner", "admin", "member"];
const selectCls =
  "h-8 rounded-md border border-slate-200 bg-white px-2 text-[12.5px] text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-200";

function statusPill(status: string): string {
  if (status === "active") return "bg-emerald-50 text-emerald-700 border-emerald-200";
  return "bg-rose-50 text-rose-700 border-rose-200";
}

export default function OrgUsers() {
  const [users, setUsers] = useState<OrgMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  const [confirmRemove, setConfirmRemove] = useState<OrgMember | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [inv, setInv] = useState({ email: "", name: "", role: "member" });
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setUsers(await listUsers());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load users");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function patch(userId: string, body: { role?: string; status?: string }) {
    setPending(userId);
    setError(null);
    try {
      await updateUser(userId, body);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed");
    } finally {
      setPending(null);
    }
  }

  async function doRemove() {
    if (!confirmRemove) return;
    setPending(confirmRemove.userId);
    try {
      await removeUser(confirmRemove.userId);
      setConfirmRemove(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Remove failed");
    } finally {
      setPending(null);
    }
  }

  async function doInvite() {
    setAdding(true);
    setError(null);
    try {
      await inviteUser(inv);
      setAddOpen(false);
      setInv({ email: "", name: "", role: "member" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invite failed");
    } finally {
      setAdding(false);
    }
  }

  return (
    <AppShell
      title="Team & Access"
      subtitle="Members of your organization and their access"
      actions={
        <Button size="sm" onClick={() => setAddOpen(true)}>
          <UserPlus className="w-4 h-4" /> Add employee
        </Button>
      }
    >
      {error && (
        <div className="mb-5 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-[13px] text-rose-700">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-24 text-slate-400">
          <Loader2 className="w-6 h-6 animate-spin" />
        </div>
      ) : (
        <div className="rounded-xl border border-slate-200/70 bg-white shadow-sm overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Member</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Access</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {users.map((u) => {
                const busy = pending === u.userId;
                const active = u.status === "active";
                return (
                  <TableRow key={u.userId}>
                    <TableCell>
                      <div className="text-[13.5px] font-medium text-slate-900">{u.name || u.email}</div>
                      <div className="text-[11.5px] text-slate-400">{u.email}</div>
                    </TableCell>
                    <TableCell>
                      <select
                        className={selectCls}
                        value={u.role}
                        disabled={busy}
                        onChange={(e) => void patch(u.userId, { role: e.target.value })}
                      >
                        {ROLES.map((r) => (
                          <option key={r} value={r}>{r}</option>
                        ))}
                      </select>
                    </TableCell>
                    <TableCell>
                      <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-semibold capitalize ${statusPill(u.status)}`}>
                        {u.status}
                      </span>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center justify-end gap-2">
                        {active ? (
                          <Button variant="outline" size="sm" disabled={busy} onClick={() => void patch(u.userId, { status: "suspended" })}>
                            <Pause className="w-3.5 h-3.5" /> Stop at next term
                          </Button>
                        ) : (
                          <Button variant="outline" size="sm" disabled={busy} onClick={() => void patch(u.userId, { status: "active" })}>
                            <Play className="w-3.5 h-3.5" /> Reactivate
                          </Button>
                        )}
                        <Button variant="ghost" size="icon-sm" className="text-rose-500 hover:text-rose-700 hover:bg-rose-50" disabled={busy} onClick={() => setConfirmRemove(u)}>
                          <Trash2 className="w-4 h-4" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
              {users.length === 0 && (
                <TableRow>
                  <TableCell colSpan={4} className="text-center text-[13px] text-slate-400 py-10">
                    No members yet.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      )}

      {/* Remove confirm */}
      <Dialog open={!!confirmRemove} onOpenChange={(o) => !o && setConfirmRemove(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove member</DialogTitle>
            <DialogDescription>
              Remove {confirmRemove?.name || confirmRemove?.email} from the organization? They will lose access.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmRemove(null)}>Cancel</Button>
            <Button variant="destructive" onClick={() => void doRemove()} disabled={pending === confirmRemove?.userId}>
              {pending === confirmRemove?.userId ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              Remove
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Add employee */}
      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add an employee</DialogTitle>
            <DialogDescription>Invite a new member to your organization.</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="iemail" className="text-[13px] text-slate-700">Email</Label>
              <Input id="iemail" type="email" value={inv.email} onChange={(e) => setInv({ ...inv, email: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="iname" className="text-[13px] text-slate-700">Name</Label>
              <Input id="iname" value={inv.name} onChange={(e) => setInv({ ...inv, name: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="irole" className="text-[13px] text-slate-700">Role</Label>
              <select id="irole" className={selectCls + " h-9 w-full"} value={inv.role} onChange={(e) => setInv({ ...inv, role: e.target.value })}>
                {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAddOpen(false)} disabled={adding}>Cancel</Button>
            <Button onClick={() => void doInvite()} disabled={adding || !inv.email.trim() || !inv.name.trim()}>
              {adding ? <Loader2 className="w-4 h-4 animate-spin" /> : <UserPlus className="w-4 h-4" />}
              Add
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}
