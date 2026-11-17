import { useCallback, useEffect, useMemo, useState } from "react";
import { Building2, Copy, ShieldCheck, UserPlus, Users } from "lucide-react";
import {
  acceptOrganizationInvitation,
  apiErrorMessage,
  createOrganization,
  fetchOrganizationMembers,
  fetchOrganizations,
  inviteOrganizationMember,
  removeOrganizationMembership,
  updateOrganizationMembership,
  type Organization,
  type OrganizationInvitation,
  type OrganizationMember,
  type OrganizationRole,
} from "@/lib/api";

const ROLES: OrganizationRole[] = ["owner", "admin", "operator", "analyst", "viewer"];
const INVITABLE_ROLES: OrganizationRole[] = ["admin", "operator", "analyst", "viewer"];

export default function OrganizationPanel() {
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [members, setMembers] = useState<OrganizationMember[]>([]);
  const [workspaceName, setWorkspaceName] = useState("");
  const [inviteUsername, setInviteUsername] = useState("");
  const [inviteRole, setInviteRole] = useState<OrganizationRole>("viewer");
  const [acceptToken, setAcceptToken] = useState("");
  const [createdInvitation, setCreatedInvitation] = useState<OrganizationInvitation | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const selected = useMemo(
    () => organizations.find((organization) => organization.id === selectedId) ?? null,
    [organizations, selectedId],
  );
  const canInvite = selected?.permissions.includes("members.invite") ?? false;
  const canUpdateMembers = selected?.permissions.includes("members.update") ?? false;
  const canRemoveMembers = selected?.permissions.includes("members.remove") ?? false;

  const loadOrganizations = useCallback(async (preferredId?: string) => {
    const rows = await fetchOrganizations();
    setOrganizations(rows);
    setSelectedId((current) => {
      const candidate = preferredId ?? current;
      return rows.some((row) => row.id === candidate) ? candidate : rows[0]?.id ?? "";
    });
  }, []);

  const loadMembers = useCallback(async (organizationId: string) => {
    if (!organizationId) {
      setMembers([]);
      return;
    }
    setMembers(await fetchOrganizationMembers(organizationId));
  }, []);

  useEffect(() => {
    void loadOrganizations().catch((error) => {
      setMessage({ type: "error", text: apiErrorMessage(error, "Unable to load organizations.") });
    });
  }, [loadOrganizations]);

  useEffect(() => {
    setCreatedInvitation(null);
    void loadMembers(selectedId).catch((error) => {
      setMembers([]);
      setMessage({ type: "error", text: apiErrorMessage(error, "Unable to load organization members.") });
    });
  }, [loadMembers, selectedId]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setMessage(null);
    try {
      await action();
    } catch (error) {
      setMessage({ type: "error", text: apiErrorMessage(error, "Organization update failed.") });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mb-6 rounded border border-[var(--border)] bg-[var(--background)] p-4" aria-labelledby="organizations-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-[var(--primary)]" aria-hidden="true" />
            <h3 id="organizations-heading" className="text-sm font-semibold">Organizations & access</h3>
          </div>
          <p className="mt-1 text-xs text-[var(--muted-foreground)]">
            Roles deny access by default. Owners and admins can manage workspace membership.
          </p>
        </div>
        {organizations.length > 0 && (
          <label className="text-xs text-[var(--muted-foreground)]">
            Active workspace
            <select
              className="ml-2 rounded border bg-[var(--input)] px-2 py-1 text-sm text-[var(--foreground)]"
              value={selectedId}
              onChange={(event) => setSelectedId(event.target.value)}
            >
              {organizations.map((organization) => (
                <option key={organization.id} value={organization.id}>
                  {organization.name} · {organization.role}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {message && (
        <div
          className={`mt-3 rounded border p-2 text-xs ${
            message.type === "success"
              ? "border-green-600 bg-green-600/10 text-green-400"
              : "border-red-600 bg-red-600/10 text-red-400"
          }`}
          role={message.type === "error" ? "alert" : "status"}
        >
          {message.text}
        </div>
      )}

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <div className="rounded border border-[var(--border)] p-3">
          <div className="mb-3 flex items-center gap-2">
            <Users className="h-4 w-4 text-[var(--muted-foreground)]" aria-hidden="true" />
            <h4 className="text-sm font-medium">Members</h4>
          </div>
          {!selected ? (
            <p className="text-xs text-[var(--muted-foreground)]">No organization is available.</p>
          ) : (
            <div className="space-y-2">
              {members.map((member) => (
                <div key={String(member.id)} className="flex flex-wrap items-center justify-between gap-2 rounded bg-[var(--secondary)] px-3 py-2">
                  <div>
                    <div className="text-sm font-medium">{member.username}</div>
                    <div className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)]">
                      {member.role}
                    </div>
                  </div>
                  {canUpdateMembers && (
                    <div className="flex items-center gap-2">
                      {(selected.role === "owner" || !["owner", "admin"].includes(member.role)) && (
                        <select
                          aria-label={`Role for ${member.username}`}
                          className="rounded border bg-[var(--input)] px-2 py-1 text-xs"
                          disabled={busy}
                          value={member.role}
                          onChange={(event) => {
                            const role = event.target.value as OrganizationRole;
                            void run(async () => {
                              await updateOrganizationMembership(selected.id, member.id, role);
                              await loadMembers(selected.id);
                              setMessage({ type: "success", text: `${member.username} is now ${role}.` });
                            });
                          }}
                        >
                          {(selected.role === "owner" ? ROLES : INVITABLE_ROLES.filter((role) => role !== "admin"))
                            .map((role) => <option key={role} value={role}>{role}</option>)}
                        </select>
                      )}
                      {canRemoveMembers
                        && member.role !== "owner"
                        && (selected.role === "owner" || member.role !== "admin") && (
                        <button
                          className="text-xs text-red-400 hover:text-red-300"
                          disabled={busy}
                          onClick={() => void run(async () => {
                            await removeOrganizationMembership(selected.id, member.id);
                            await loadMembers(selected.id);
                            setMessage({ type: "success", text: `${member.username} removed.` });
                          })}
                          type="button"
                        >
                          Remove
                        </button>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="space-y-4">
          {selected && canInvite && (
            <div className="rounded border border-[var(--border)] p-3">
              <div className="mb-2 flex items-center gap-2">
                <UserPlus className="h-4 w-4 text-[var(--muted-foreground)]" aria-hidden="true" />
                <h4 className="text-sm font-medium">Invite registered user</h4>
              </div>
              <div className="flex flex-wrap gap-2">
                <input
                  aria-label="Invite username"
                  className="min-w-40 flex-1 rounded border bg-[var(--input)] px-2 py-1.5 text-sm"
                  placeholder="username"
                  value={inviteUsername}
                  onChange={(event) => setInviteUsername(event.target.value)}
                />
                <select
                  aria-label="Invitation role"
                  className="rounded border bg-[var(--input)] px-2 py-1.5 text-sm"
                  value={inviteRole}
                  onChange={(event) => setInviteRole(event.target.value as OrganizationRole)}
                >
                  {INVITABLE_ROLES.map((role) => <option key={role} value={role}>{role}</option>)}
                </select>
                <button
                  className="rounded bg-[var(--primary)] px-3 py-1.5 text-xs font-medium text-[var(--primary-foreground)] disabled:opacity-50"
                  disabled={busy || !inviteUsername.trim()}
                  onClick={() => void run(async () => {
                    const invitation = await inviteOrganizationMember(
                      selected.id,
                      inviteUsername.trim(),
                      inviteRole,
                    );
                    setCreatedInvitation(invitation);
                    setInviteUsername("");
                    setMessage({ type: "success", text: "Invitation created. Share the one-time token securely." });
                  })}
                  type="button"
                >
                  Invite
                </button>
              </div>
              {createdInvitation && (
                <div className="mt-3 rounded border border-amber-500/50 bg-amber-500/10 p-2">
                  <div className="text-xs font-medium text-amber-300">One-time invitation token</div>
                  <div className="mt-1 flex items-center gap-2">
                    <code className="min-w-0 flex-1 break-all text-[11px]">{createdInvitation.token}</code>
                    <button
                      aria-label="Copy invitation token"
                      className="rounded bg-[var(--secondary)] p-1.5"
                      onClick={() => void navigator.clipboard.writeText(createdInvitation.token)}
                      type="button"
                    >
                      <Copy className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

          <div className="rounded border border-[var(--border)] p-3">
            <h4 className="text-sm font-medium">Accept invitation</h4>
            <p className="my-2 text-xs text-[var(--muted-foreground)]">
              Paste the one-time token sent by an organization owner or admin.
            </p>
            <div className="flex gap-2">
              <input
                aria-label="Invitation token"
                className="min-w-0 flex-1 rounded border bg-[var(--input)] px-2 py-1.5 text-sm"
                placeholder="invitation token"
                type="password"
                value={acceptToken}
                onChange={(event) => setAcceptToken(event.target.value)}
              />
              <button
                className="rounded bg-[var(--secondary)] px-3 py-1.5 text-xs disabled:opacity-50"
                disabled={busy || !acceptToken.trim()}
                onClick={() => void run(async () => {
                  const membership = await acceptOrganizationInvitation(acceptToken.trim());
                  setAcceptToken("");
                  await loadOrganizations(membership.organization_id);
                  setMessage({ type: "success", text: "Invitation accepted." });
                })}
                type="button"
              >
                Join
              </button>
            </div>
          </div>

          <div className="rounded border border-[var(--border)] p-3">
            <div className="mb-2 flex items-center gap-2">
              <Building2 className="h-4 w-4 text-[var(--muted-foreground)]" aria-hidden="true" />
              <h4 className="text-sm font-medium">Create workspace</h4>
            </div>
            <div className="flex gap-2">
              <input
                aria-label="New workspace name"
                className="min-w-0 flex-1 rounded border bg-[var(--input)] px-2 py-1.5 text-sm"
                placeholder="Research team"
                value={workspaceName}
                onChange={(event) => setWorkspaceName(event.target.value)}
              />
              <button
                className="rounded bg-[var(--secondary)] px-3 py-1.5 text-xs disabled:opacity-50"
                disabled={busy || !workspaceName.trim()}
                onClick={() => void run(async () => {
                  const organization = await createOrganization(workspaceName.trim());
                  setWorkspaceName("");
                  await loadOrganizations(organization.id);
                  setMessage({ type: "success", text: `${organization.name} created.` });
                })}
                type="button"
              >
                Create
              </button>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
