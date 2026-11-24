"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CopyIcon, Loader2Icon, MailPlusIcon, ShieldCheckIcon, ShieldIcon, UserPlusIcon } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { DataTable, type Column } from "@/components/data/data-table";
import { PageHeader } from "@/components/data/page-header";
import { ErrorState } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { Checkbox, Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useApiQuery, useBizQuery, useBusiness } from "@/hooks/use-business";
import { api, ApiError } from "@/lib/api";
import { dateTime, initials, titleCase } from "@/lib/format";

interface Role { id: string; code: string | null; name: string; description: string | null; is_system: boolean; permissions: string[] }
interface Member { id: string; user: { id: string; email: string; full_name: string; status: string; last_login_at: string | null; mfa_enabled: boolean };
  role: Role; status: string; all_branches: boolean; branch_ids: string[]; grant_permissions: string[]; deny_permissions: string[]; effective_permissions: string[]; created_at: string }
interface Perm { code: string; group: string; description: string }
interface Branch { id: string; code: string; name: string }
interface Audit { id: string; action: string; entity_type: string; entity_id: string | null; actor_user_id: string | null; changes: Record<string, unknown> | null; ip_address: string | null; created_at: string }

export default function UsersPage() {
  const { can, isOwner } = useBusiness();
  return (
    <>
      <PageHeader title="Users & permissions" description="Up to 30 people per business. Each sees only what their role and branches allow." />
      <Tabs defaultValue="members">
        <TabsList><TabsTrigger value="members">Members</TabsTrigger><TabsTrigger value="roles">Roles</TabsTrigger>
          {(isOwner || can("audit.view")) && <TabsTrigger value="activity">Activity</TabsTrigger>}</TabsList>
        <TabsContent value="members"><Members /></TabsContent>
        <TabsContent value="roles"><Roles /></TabsContent>
        <TabsContent value="activity"><Activity /></TabsContent>
      </Tabs>
    </>
  );
}

function inviteLink(token: string) {
  return `${window.location.origin}/accept-invite?token=${encodeURIComponent(token)}`;
}

function Members() {
  const { can, isOwner, base, id } = useBusiness();
  const qc = useQueryClient();
  const manage = isOwner || can("users.manage");
  const [invite, setInvite] = useState(false);
  const [edit, setEdit] = useState<Member | null>(null);
  const [link, setLink] = useState<string | null>(null);
  const act = useMutation({
    mutationFn: ({ path }: { path: string }) => api<{ invite_token?: string }>(`${base}/members/${path}`, { body: {} }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["biz", id, "members"] }); if (r.invite_token) setLink(inviteLink(r.invite_token)); else toast.success("Updated"); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  const cols: Column<Member>[] = [
    { key: "u", header: "Person", cell: (m) => (
      <div className="flex items-center gap-3"><span className="flex size-8 items-center justify-center rounded-full bg-blue-soft text-xs font-semibold text-blue">{initials(m.user.full_name)}</span>
        <div><p className="font-medium">{m.user.full_name}</p><p className="text-xs text-muted-foreground">{m.user.email}</p></div></div>) },
    { key: "r", header: "Role", cell: (m) => <Badge variant="blue">{m.role.name}</Badge> },
    { key: "b", header: "Branches", cell: (m) => m.all_branches ? "All" : `${m.branch_ids.length} branch${m.branch_ids.length === 1 ? "" : "es"}`, hideOnMobile: true },
    { key: "m", header: "2FA", cell: (m) => m.user.mfa_enabled ? <ShieldCheckIcon className="size-4 text-primary" /> : <ShieldIcon className="size-4 text-muted-foreground/50" />, hideOnMobile: true },
    { key: "s", header: "Status", cell: (m) => <Badge variant={m.status === "active" ? "default" : m.status === "suspended" ? "destructive" : "warning"}>{titleCase(m.user.status === "invited" ? "invited" : m.status)}</Badge> },
    { key: "l", header: "Last sign-in", cell: (m) => m.user.last_login_at ? dateTime(m.user.last_login_at) : "Never", hideOnMobile: true },
    { key: "a", header: "", align: "right", cell: (m) => manage ? (
      <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
        {m.user.status === "invited" && <Button size="sm" variant="ghost" onClick={() => act.mutate({ path: `${m.id}/invite` })}><MailPlusIcon /> Invite link</Button>}
        {m.status === "active" ? <Button size="sm" variant="ghost" onClick={() => confirm(`Suspend ${m.user.full_name}? Their sessions end immediately.`) && act.mutate({ path: `${m.id}/suspend` })}>Suspend</Button>
          : <Button size="sm" variant="ghost" onClick={() => act.mutate({ path: `${m.id}/reactivate` })}>Reactivate</Button>}
      </div>) : null },
  ];
  return (
    <>
      {manage && <div className="mb-3 flex justify-end"><Button onClick={() => setInvite(true)}><UserPlusIcon /> Invite member</Button></div>}
      <DataTable<Member> queryKey={["members"]} path="/members" columns={cols} searchParam={null} onRowClick={manage ? setEdit : undefined} />
      <InviteDialog open={invite} onOpenChange={setInvite} onInvited={(t) => t && setLink(inviteLink(t))} />
      {edit && <MemberDialog member={edit} onClose={() => setEdit(null)} />}
      <Dialog open={!!link} onOpenChange={(v) => !v && setLink(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>Invitation link</DialogTitle><DialogDescription>Send this link to the person privately (WhatsApp or e-mail). It works once and expires in 72 hours.</DialogDescription></DialogHeader>
          <pre className="rounded-lg border bg-muted p-3 font-mono text-xs break-all whitespace-pre-wrap">{link}</pre>
          <DialogFooter><Button onClick={() => { void navigator.clipboard.writeText(link ?? ""); toast.success("Copied"); }}><CopyIcon /> Copy link</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function useRolesAndBranches(enabled = true) {
  const roles = useBizQuery<Role[]>(["roles"], "/roles", undefined, { enabled });
  const branches = useBizQuery<Branch[]>(["branches"], "/branches", undefined, { enabled });
  return { roles, branches };
}

function BranchScope({ all, setAll, ids, setIds, branches }: { all: boolean; setAll: (v: boolean) => void; ids: string[]; setIds: (v: string[]) => void; branches: Branch[] }) {
  return (
    <div className="rounded-lg border p-3">
      <label className="flex items-center justify-between text-sm font-medium">All branches <Switch checked={all} onCheckedChange={setAll} /></label>
      {!all && <div className="mt-2 grid gap-1 sm:grid-cols-2">{branches.map((b) => (
        <label key={b.id} className="flex items-center gap-2 text-sm"><Checkbox checked={ids.includes(b.id)} onCheckedChange={(v) => setIds(v ? [...ids, b.id] : ids.filter((x) => x !== b.id))} />{b.code} — {b.name}</label>))}</div>}
    </div>
  );
}

function InviteDialog({ open, onOpenChange, onInvited }: { open: boolean; onOpenChange: (v: boolean) => void; onInvited: (token?: string | null) => void }) {
  const { base, id } = useBusiness();
  const qc = useQueryClient();
  const { roles, branches } = useRolesAndBranches(open);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [all, setAll] = useState(true);
  const [ids, setIds] = useState<string[]>([]);
  const save = useMutation({
    mutationFn: () => api<{ invite_token: string | null }>(`${base}/members`, { body: { email, full_name: name || undefined, role_id: role || roles.data?.[0]?.id, all_branches: all, branch_ids: all ? [] : ids } }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["biz", id, "members"] }); onOpenChange(false); setEmail(""); setName(""); if (r.invite_token) onInvited(r.invite_token); else toast.success("Added — they already have an account"); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader><DialogTitle>Invite a member</DialogTitle></DialogHeader>
        <div className="grid gap-4">
          <Field label="E-mail"><Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus /></Field>
          <Field label="Full name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Role" hint={roles.data?.find((r) => r.id === role)?.description ?? undefined}>
            <NativeSelect value={role} onChange={(e) => setRole(e.target.value)}>{roles.data?.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</NativeSelect>
          </Field>
          <BranchScope all={all} setAll={setAll} ids={ids} setIds={setIds} branches={branches.data ?? []} />
        </div>
        <DialogFooter><Button disabled={!email.includes("@") || save.isPending} onClick={() => save.mutate()}>{save.isPending && <Loader2Icon className="animate-spin" />} Invite</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function MemberDialog({ member, onClose }: { member: Member; onClose: () => void }) {
  const { base, id } = useBusiness();
  const qc = useQueryClient();
  const { roles, branches } = useRolesAndBranches();
  const perms = useApiQuery<Perm[]>(["permissions"], "/permissions");
  const [role, setRole] = useState(member.role.id);
  const [all, setAll] = useState(member.all_branches);
  const [ids, setIds] = useState(member.branch_ids);
  const [grant, setGrant] = useState(member.grant_permissions);
  const [deny, setDeny] = useState(member.deny_permissions);
  const rolePerms = new Set(roles.data?.find((r) => r.id === role)?.permissions ?? []);
  const save = useMutation({
    mutationFn: () => api(`${base}/members/${member.id}`, { method: "PATCH", body: { role_id: role, all_branches: all, branch_ids: all ? [] : ids, grant_permissions: grant, deny_permissions: deny } }),
    onSuccess: () => { toast.success("Access updated"); qc.invalidateQueries({ queryKey: ["biz", id, "members"] }); onClose(); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  const groups = useMemo(() => [...new Set((perms.data ?? []).map((p) => p.group))], [perms.data]);
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide>
        <DialogHeader><DialogTitle>{member.user.full_name}</DialogTitle><DialogDescription>{member.user.email}</DialogDescription></DialogHeader>
        <Field label="Role"><NativeSelect value={role} onChange={(e) => setRole(e.target.value)}>{roles.data?.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</NativeSelect></Field>
        <BranchScope all={all} setAll={setAll} ids={ids} setIds={setIds} branches={branches.data ?? []} />
        <div>
          <p className="mb-1 text-sm font-medium">Exceptions to the role</p>
          <p className="mb-2 text-xs text-muted-foreground">Grant a permission the role lacks, or deny one it has. Nobody can grant more than they hold.</p>
          <div className="max-h-72 space-y-3 overflow-y-auto rounded-lg border p-3">
            {groups.map((g) => (
              <div key={g}><p className="mb-1 text-xs font-semibold text-muted-foreground uppercase">{g}</p>
                {perms.data!.filter((p) => p.group === g).map((p) => {
                  const state = deny.includes(p.code) ? "deny" : grant.includes(p.code) ? "grant" : "role";
                  const effective = state === "grant" || (state === "role" && rolePerms.has(p.code));
                  return (
                    <div key={p.code} className="flex items-center justify-between gap-2 py-0.5 text-sm">
                      <span className={effective ? "" : "text-muted-foreground"}>{p.description} <span className="font-mono text-[11px] text-muted-foreground">{p.code}</span></span>
                      <NativeSelect className="h-7 w-28 text-xs" value={state} onChange={(e) => {
                        const v = e.target.value;
                        setGrant((s) => v === "grant" ? [...s.filter((x) => x !== p.code), p.code] : s.filter((x) => x !== p.code));
                        setDeny((s) => v === "deny" ? [...s.filter((x) => x !== p.code), p.code] : s.filter((x) => x !== p.code));
                      }}><option value="role">{rolePerms.has(p.code) ? "Role: yes" : "Role: no"}</option><option value="grant">Grant</option><option value="deny">Deny</option></NativeSelect>
                    </div>
                  );
                })}</div>
            ))}
          </div>
        </div>
        <DialogFooter><Button variant="outline" onClick={onClose}>Cancel</Button><Button disabled={save.isPending} onClick={() => save.mutate()}>{save.isPending && <Loader2Icon className="animate-spin" />} Save access</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Roles() {
  const { can, isOwner, base, id } = useBusiness();
  const qc = useQueryClient();
  const roles = useBizQuery<Role[]>(["roles"], "/roles");
  const perms = useApiQuery<Perm[]>(["permissions"], "/permissions");
  const [editing, setEditing] = useState<Role | "new" | null>(null);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const manage = isOwner || can("roles.manage");
  const open = (r: Role | "new") => { setEditing(r); setName(r === "new" ? "" : r.name); setDesc(r === "new" ? "" : r.description ?? ""); setPicked(r === "new" ? [] : r.permissions); };
  const save = useMutation({
    mutationFn: () => editing === "new" ? api(`${base}/roles`, { body: { name, description: desc || undefined, permissions: picked } })
      : api(`${base}/roles/${(editing as Role).id}`, { method: "PATCH", body: { name, description: desc || undefined, permissions: picked } }),
    onSuccess: () => { toast.success("Role saved"); qc.invalidateQueries({ queryKey: ["biz", id, "roles"] }); setEditing(null); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  if (roles.isLoading) return <Skeleton className="h-40" />;
  if (roles.error) return <Card><ErrorState error={roles.error} /></Card>;
  const groups = [...new Set((perms.data ?? []).map((p) => p.group))];
  return (
    <>
      {manage && <div className="mb-3 flex justify-end"><Button onClick={() => open("new")}>New custom role</Button></div>}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {roles.data!.map((r) => (
          <Card key={r.id} className={manage && !r.is_system ? "cursor-pointer hover:border-primary/40" : ""} onClick={() => manage && !r.is_system && open(r)}>
            <CardHeader className="flex-row items-start justify-between"><CardTitle>{r.name}</CardTitle><Badge variant={r.is_system ? "secondary" : "blue"}>{r.is_system ? "Built-in" : "Custom"}</Badge></CardHeader>
            <CardContent className="space-y-2 text-sm text-muted-foreground"><p>{r.description}</p><p className="text-xs">{r.permissions.length} permissions</p></CardContent>
          </Card>
        ))}
      </div>
      <Dialog open={!!editing} onOpenChange={(v) => !v && setEditing(null)}>
        <DialogContent wide>
          <DialogHeader><DialogTitle>{editing === "new" ? "New custom role" : "Edit role"}</DialogTitle></DialogHeader>
          <div className="grid gap-3 sm:grid-cols-2"><Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
            <Field label="Description"><Textarea value={desc} onChange={(e) => setDesc(e.target.value)} className="min-h-9" /></Field></div>
          <div className="grid max-h-[50vh] gap-4 overflow-y-auto rounded-lg border p-3 sm:grid-cols-2">
            {groups.map((g) => (
              <div key={g}><p className="mb-1 text-xs font-semibold text-muted-foreground uppercase">{g}</p>
                {perms.data!.filter((p) => p.group === g).map((p) => (
                  <label key={p.code} className="flex items-start gap-2 py-0.5 text-sm"><Checkbox className="mt-0.5" checked={picked.includes(p.code)} onCheckedChange={(v) => setPicked((s) => v ? [...s, p.code] : s.filter((x) => x !== p.code))} />{p.description}</label>
                ))}</div>
            ))}
          </div>
          <DialogFooter><Button disabled={name.length < 2 || save.isPending} onClick={() => save.mutate()}>{save.isPending && <Loader2Icon className="animate-spin" />} Save role</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function Activity() {
  const cols: Column<Audit>[] = [
    { key: "t", header: "When", cell: (a) => dateTime(a.created_at) },
    { key: "a", header: "Action", cell: (a) => <span className="font-mono text-xs">{a.action}</span> },
    { key: "e", header: "Record", cell: (a) => titleCase(a.entity_type), hideOnMobile: true },
    { key: "c", header: "Details", cell: (a) => <span className="block max-w-md truncate text-xs text-muted-foreground">{a.changes ? JSON.stringify(a.changes) : ""}</span>, hideOnMobile: true },
    { key: "i", header: "IP", cell: (a) => <span className="text-xs text-muted-foreground">{a.ip_address ?? ""}</span>, hideOnMobile: true },
  ];
  return <DataTable<Audit> queryKey={["audit"]} path="/audit-logs" columns={cols} searchParam={null} pageSize={50} />;
}
