/** Organization member management API (client-admin). */
import { adminFetch } from "./adminHttp";

export interface OrgMember {
  userId: string;
  email: string;
  name: string;
  role: string; // owner | admin | member
  status: string; // active | suspended | inactive
  createdAt: string;
}

export function listUsers(): Promise<OrgMember[]> {
  return adminFetch<OrgMember[]>("/api/v1/users");
}

export function inviteUser(input: { email: string; name: string; role: string }): Promise<OrgMember> {
  return adminFetch<OrgMember>("/api/v1/users", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

/** Update a member's role and/or account status (status = access control). */
export function updateUser(
  userId: string,
  patch: { role?: string; status?: string },
): Promise<OrgMember> {
  return adminFetch<OrgMember>(`/api/v1/users/${encodeURIComponent(userId)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export function removeUser(userId: string): Promise<{ success: true }> {
  return adminFetch<{ success: true }>(`/api/v1/users/${encodeURIComponent(userId)}`, {
    method: "DELETE",
  });
}
