import time

from techdiag.collectors.base import Collector
from techdiag.core.models import CheckResult, Context, Session, Status, validate_identification
from techdiag.core.orchestrator import Orchestrator
from techdiag.infra.database import Database


def test_identification_requires_client_and_ticket():
    assert len(validate_identification("", "", "Ana", "PC")) == 2
    assert validate_identification("Acme", "T-1", "Ana", "PC") == []
    assert any("ticket" in e for e in validate_identification("Acme", " ", "Ana", "PC"))


def test_result_roundtrip():
    r = CheckResult("k", "T", Status.ALERT, "s", data={"a": [1, {"b": None}]}, problems=["p"])
    assert CheckResult.from_dict(r.to_dict()) == r


def test_database_sessions_results_actions(tmp_path):
    db = Database(tmp_path / "h.db")
    s = Session("Acme", "T-9", "Ana", "Dell X")
    sid = db.create_session(s)
    db.save_result(sid, CheckResult("hardware", "Hardware", Status.OK, "ok", data={"x": 1}))
    db.log_action(sid, "Limpeza", "ok", True, "temp_files")
    db.close()
    db2 = Database(tmp_path / "h.db")   # persistencia entre aberturas
    assert db2.get_session(sid).client == "Acme"
    assert db2.load_results(sid)["hardware"].data == {"x": 1}
    assert db2.list_actions(sid)[0]["resolves"] == "temp_files"
    assert [x.id for x in db2.list_sessions()] == [sid]


class Boom(Collector):
    key, title = "boom", "Boom"

    def run(self, ctx):
        raise RuntimeError("falhou")


class Slow(Collector):
    key, title = "slow", "Slow"

    def run(self, ctx):
        time.sleep(0.05)
        return self.new(Status.OK, "ok")


def test_orchestrator_isolates_failures(tmp_path):
    db = Database(tmp_path / "h.db")
    ctx = Context(session=Session("A", "1"))
    db.create_session(ctx.session)
    seen = []
    o = Orchestrator([Boom(), Slow()], ctx, on_update=seen.append, db=db)
    res = o.run_blocking()
    assert res["boom"].status == Status.ERROR and "falhou" in res["boom"].reason
    assert res["slow"].status == Status.OK
    assert {r.status for r in seen if r.key == "slow"} >= {Status.PENDING, Status.RUNNING, Status.OK}
    assert set(db.load_results(ctx.session.id)) == {"boom", "slow"}
    o.shutdown()


def test_collectors_report_reason_off_windows():
    from techdiag.collectors import all_collectors
    for c in all_collectors():
        r = c.execute(Context())
        assert r.status in (Status.OK, Status.ALERT, Status.ERROR)
        if r.status == Status.ERROR:
            assert r.reason   # nunca falha sem explicar o motivo
