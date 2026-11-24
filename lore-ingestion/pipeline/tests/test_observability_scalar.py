"""_scalar must return 0 for a table that does not exist, and keep the session
usable — a partly-onboarded tenant has only the layers it has run."""
import sqlalchemy as sa
from control import observability as obs


def test_missing_table_is_zero_and_session_survives(tmp_path):
    eng = sa.create_engine(f"sqlite:///{tmp_path}/t.db")
    with sa.orm.Session(eng) as ses:
        ses.execute(sa.text("CREATE TABLE present (org TEXT)"))
        ses.execute(sa.text("INSERT INTO present VALUES ('acme')"))
        ses.commit()
        # sqlite raises OperationalError, not psycopg2 UndefinedTable, so drive
        # the real branch with the exception the production driver produces.
        from psycopg2 import errors
        real = obs.sa.exc.ProgrammingError
        calls = {"n": 0}
        orig = ses.execute

        def flaky(stmt, params=None):
            calls["n"] += 1
            if calls["n"] == 1:
                exc = real("SELECT 1", {}, Exception("x"))
                exc.orig = errors.UndefinedTable()
                raise exc
            return orig(stmt, params)

        ses.execute = flaky
        assert obs._scalar(ses, "SELECT count(*) FROM lore.claim_digest") == 0
        ses.execute = orig
        assert obs._scalar(ses, "SELECT count(*) FROM present WHERE org=:o", o="acme") == 1
