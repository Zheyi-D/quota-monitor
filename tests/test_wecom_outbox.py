import json
from datetime import datetime, timezone
from quota_monitor.wecom_outbox import enqueue, deliver


def test_failure_restart_success_and_expiry():
    now = datetime(2026, 9, 11, tzinfo=timezone.utc).timestamp()
    row = (("09/12/2026", "RHK", "R"), "quota-r", "quota-y")
    snapshot = {row[0]: "quota-y"}
    pending = enqueue([], {"newly_available": [row]}, now=now, enabled=True)
    persisted=[];sent=[]
    def save(items):persisted.append(json.loads(json.dumps(items)));return True
    def send(url,msg):sent.append(url);return url=='a'
    kwargs=dict(format_message=str,persist=save)
    pending,status=deliver(pending,snapshot,['a','b'],now=now,send=send,**kwargs)
    assert status=='retry_pending'
    assert len(persisted[0])==1
    pending=json.loads(json.dumps(pending))
    pending,status=deliver(pending,snapshot,['a','b'],now=now+31,
        send=lambda u,m:sent.append(u) or True,**kwargs)
    assert pending==[] and status=='OK' and sent==['a','b','b']
    pending=enqueue([],{'newly_available':[row]},now=now,enabled=True)
    pending,status=deliver(pending,snapshot,['a'],now=now+901,send=send,**kwargs)
    assert not pending and sent==['a','b','b']


def test_not_available_or_not_persisted_never_sends():
    now=datetime(2026,9,11,tzinfo=timezone.utc).timestamp()
    row=(("09/12/2026","RHK","R"),"quota-r","quota-y")
    pending=enqueue([],{'newly_available':[row]},now=now,enabled=True)
    def forbidden(*a):raise AssertionError('no sending')
    result,status=deliver(pending,{row[0]:'quota-y'},['a'],now=now,
        format_message=str,send=forbidden,persist=lambda _:False)
    assert result and status=='persistence_failed'
    result,status=deliver(pending,{},['a'],now=now,format_message=str,
        send=forbidden,persist=lambda _:True)
    assert not result


def test_ci_persistence_result_controls_delivery(tmp_path, monkeypatch):
    import ci_run
    from types import SimpleNamespace
    monkeypatch.setenv("GITHUB_REPOSITORY", "test/isolated")
    monkeypatch.setattr(ci_run.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="synthetic"))
    state_file=str(tmp_path / "state.json")
    assert ci_run._save_state_remote(state_file, {}, {"pending_wecom": []}) is False
    monkeypatch.setattr(ci_run.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="{}", stderr=""))
    assert ci_run._save_state_remote(state_file, {}, {"pending_wecom": []}) is True
    assert json.loads((tmp_path / "state.json").read_text())["pending_wecom"] == []


def test_disabled_start_does_not_backfill():
    assert enqueue([], {"newly_available": [(('09/12/2026','RHK','R'),'quota-r','quota-y')]}, now=0, enabled=False) == []
