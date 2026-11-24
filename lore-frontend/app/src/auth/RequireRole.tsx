/** Route guard: gates a subtree to a platform role. Users lacking the role are
 *  bounced to their own role home (never shown a forbidden screen). Assumes it
 *  is nested inside <RequireAuth/>, so identity is present. */
import { Navigate, Outlet } from "react-router-dom";
import { useAuth, roleHome } from "./AuthContext";

interface Props {
  require: "lore_superadmin" | "client_superadmin";
}

export default function RequireRole({ require: required }: Props) {
  const { identity } = useAuth();

  const allowed =
    required === "lore_superadmin" ? !!identity?.isLoreAdmin : !!identity?.isClientAdmin;

  if (!allowed) {
    return <Navigate to={roleHome(identity)} replace />;
  }
  return <Outlet />;
}
