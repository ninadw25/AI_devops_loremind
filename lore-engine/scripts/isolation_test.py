"""Two-client isolation test — drives lore-engine's real TenantRegistry against
the real elysia-rbac registry and two separate Neo4j instances.

This is the test that could not exist while only one database was running: with a
single graph, routing "works" no matter what, because every path leads to the
same place. Here acme and globex hold contradictory facts, so a leak is visible
rather than theoretical.

Run from lore-engine/ with the shared venv.
"""
import sys

from core.settings import Settings
from core.types import UserScopes
from domain.scopes import scope_gate, scope_params
from infra.neo4j_client import Neo4jClient
from infra.tenant_registry import TenantRegistry

RBAC = "http://localhost:3000"
FAILURES = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(name)


def decisions(client, scopes: UserScopes) -> list[str]:
    """Read decisions through the SAME fail-closed gate the query pipeline uses.

    Neo4jClient.run() returns None on error/unavailable and [] for a genuinely
    empty result. Collapsing those would let a broken connection masquerade as
    "correctly saw nothing" — the exact false pass this test exists to catch — so
    None raises instead.
    """
    rows = client.run(
        f"MATCH (d:Decision) WHERE {scope_gate('d')} RETURN d.description AS d",
        **scope_params(scopes),
    )
    if rows is None:
        raise RuntimeError("query failed or DB unavailable — not the same as an empty result")
    return sorted(r["d"] for r in rows)


def main() -> int:
    secret = sys.argv[1]
    settings = Settings(
        tenant_routing_enabled=True,
        rbac_url=RBAC,
        rbac_internal_secret=secret,
        neo4j_username="neo4j",
        # Deliberately a dead address: if routing ever falls back to the static
        # client instead of the registry, every query fails loudly instead of
        # quietly serving the wrong client's data.
        neo4j_uri="bolt://127.0.0.1:9",
        neo4j_password="not-a-real-password",
    )
    # Same dead address: the fallback must never be what answers a routed query.
    fallback = Neo4jClient(settings.neo4j_uri, "neo4j", settings.neo4j_password)
    reg = TenantRegistry(settings, fallback)

    acme_scopes = UserScopes(["slack:channel:Cacme"])
    globex_scopes = UserScopes(["slack:channel:Cglobex"])

    print("\n[1] Each client's connection resolves to its own database")
    acme = reg.neo4j_for("acme")
    globex = reg.neo4j_for("globex")
    check("acme graph reachable", acme.available)
    check("globex graph reachable", globex.available)

    print("\n[2] Each client sees its own data")
    a = decisions(acme, acme_scopes)
    g = decisions(globex, globex_scopes)
    check("acme sees exactly its own decision", a == ["Acme will move billing to Stripe"], str(a))
    check("globex sees exactly its own decision", g == ["Globex will move billing to Razorpay"], str(g))

    print("\n[3] Neither client can see the other's data (the point of the test)")
    check("acme's graph holds nothing of globex's", all("Globex" not in x for x in a), str(a))
    check("globex's graph holds nothing of acme's", all("Acme" not in x for x in g), str(g))

    print("\n[4] Right database, wrong scopes → still nothing (content gate)")
    # An acme user whose allowed scopes are globex's: routed to acme's DB, but the
    # scope gate must still refuse acme's content. Routing and the gate are two
    # independent boundaries; this checks the second one didn't come along for free.
    check("acme DB + globex scopes → empty", decisions(acme, globex_scopes) == [])
    check("acme DB + no scopes → empty", decisions(acme, UserScopes([])) == [])

    print("\n[5] Super-admin reads that client's data — and ONLY that client's")
    sa = decisions(acme, UserScopes([], super_admin=True))
    check("super-admin sees acme's data", sa == ["Acme will move billing to Stripe"], str(sa))
    check("super-admin is still tenant-bounded", all("Globex" not in x for x in sa), str(sa))

    print("\n[6] Unknown client fails closed (never falls back to a shared DB)")
    try:
        reg.neo4j_for("nosuchorg")
        check("unknown org raises", False, "returned a client instead of raising")
    except Exception as e:
        check("unknown org raises", True, type(e).__name__)

    print("\n[7] Memory database is routed per client too")
    # _resolve() renames the registry's `memoryPg` block to `memory`.
    try:
        m_acme = reg._resolve("acme")["memory"]["uri"]
        m_globex = reg._resolve("globex")["memory"]["uri"]
        check("memory DBs differ per client", m_acme != m_globex, f"{m_acme} vs {m_globex}")
    except Exception as e:
        check("memory DBs differ per client", False, repr(e))

    print("\n" + ("ALL PASSED" if not FAILURES else f"FAILED: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
