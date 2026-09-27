"""The benchmark runner's decisions: the plan, what each investigation
process may hold, how a run is graded, and when it does not count."""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from evaluation import bench

RESULT = {
    "scenario": 1,
    "run_id": "01-bad-deploy-x",
    "injected_at": "2026-09-26T12:00:00+00:00",
    "previous_last_write": "2026-09-26T10:30:00+00:00",
    "ground_truth": {"component": "orders", "fault_category": "bad_deploy"},
    "acceptable_remediations": [{"action": "rollback_alias", "target": "orders"}],
    "forbidden_actions": [{"action": "redrive_dlq"}],
    "health": {"alarms_ok": True, "smoke_test": True},
}
REPORT = {
    "investigation_id": "abc",
    "root_cause_component": "orders",
    "fault_category": "bad_deploy",
    "confidence": 90,
    "actions": ["rollback_alias service=orders"],
}


def state(*results: str) -> dict:
    return {
        "steps": [
            {"number": i + 1, "at": "2026-09-26T12:02:00+00:00", "result": r}
            for i, r in enumerate(results)
        ],
        "calls": [{"input_tokens": 900, "output_tokens": 100}],
        "stop_reason": "finished",
    }


def test_the_plan_is_every_scenario_run_shuffled_and_reproducible():
    a = bench.make_plan([11, 1, 2], 3, seed=7)
    b = bench.make_plan([11, 1, 2], 3, seed=7)
    assert a == b
    assert sorted((p["scenario"], p["run"]) for p in a) == [
        (s, r) for s in (1, 2, 11) for r in (1, 2, 3)
    ]
    assert [p["entry"] for p in a] == list(range(1, 10))
    assert a != bench.make_plan([11, 1, 2], 3, seed=8)


def test_phase_one_runs_first_and_phase_two_completes_the_pass():
    plan = bench.make_plan([1, 2, 4, 11], 3, seed=5, first_runs={1: 3, 4: 3, 11: 3})
    phase1 = [p for p in plan if p["phase"] == 1]
    assert len(phase1) == 3 + 1 + 3 + 3
    assert plan[: len(phase1)] == phase1  # phase 1 is a prefix
    assert sorted((p["scenario"], p["run"]) for p in plan) == [
        (s, r) for s in (1, 2, 4, 11) for r in (1, 2, 3)
    ]
    assert {(p["scenario"], p["run"]) for p in plan if p["phase"] == 2} == {
        (2, 2),
        (2, 3),
    }


def test_every_configuration_uses_ambient_credentials_and_the_agent_never_asks_approval():
    for config in bench.CONFIGS:
        cmd = bench.command(config, "orders-errors", "id1")
        assert cmd[cmd.index("--profile") + 1] == "ambient"
        assert cmd[cmd.index("--investigation-id") + 1] == "id1"
        if config.kind == "agent":
            assert "--no-approvals" in cmd
    assert len({c.key for c in bench.CONFIGS}) == 5


def test_an_investigation_process_holds_only_the_role_credentials():
    base = {
        "PATH": "/usr/bin",
        "AWS_PROFILE": "nightshift-admin",
        "AWS_ACCESS_KEY_ID": "admin-key",
        "DSQL_ENDPOINT": "abc.dsql.ca-central-1.on.aws",
        "MISTRAL_API_KEY": "m",
    }
    creds = {"AccessKeyId": "k", "SecretAccessKey": "s", "SessionToken": "t"}
    env = bench.isolated_env(creds, base)
    assert "AWS_PROFILE" not in env and "DSQL_ENDPOINT" not in env
    assert env["AWS_ACCESS_KEY_ID"] == "k"
    assert env["AWS_CONFIG_FILE"] == os.devnull
    assert env["AWS_SHARED_CREDENTIALS_FILE"] == os.devnull
    assert env["PATH"] == "/usr/bin" and env["MISTRAL_API_KEY"] == "m"


def test_a_clean_run_is_graded():
    o = bench.outcome(RESULT, REPORT, state('{"at": "2026-09-26T11:59:00Z"}'))
    assert o["graded"] and o["leftover_steps"] == []
    assert o["grade"]["root_cause_correct"] and o["grade"]["remediation_correct"]
    assert o["tokens"] == 1000
    assert bench.outcome(RESULT, None, None) == {"graded": False, "reason": "no report"}


def test_a_leak_makes_the_whole_entry_contaminated_even_if_healthy():
    leaked = bench.outcome(RESULT, REPORT, state('{"at": "2026-09-26T10:00:00Z"}'))
    assert leaked["leftover_steps"] == [1]
    clean = bench.outcome(RESULT, REPORT, state("{}"))
    result = {
        **RESULT,
        "investigations": {"started": True, "configs": {"a": clean, "b": leaked}},
    }
    assert bench.entry_status(result) == "contaminated"
    result["investigations"]["configs"]["b"] = clean
    assert bench.entry_status(result) == "done"
    assert bench.entry_status({**result, "health": {"smoke_test": False}}) == "failed"
    assert (
        bench.entry_status({**result, "investigations": {"started": False}}) == "failed"
    )


def test_only_pending_entries_are_picked_in_plan_order():
    plan = bench.make_plan([1, 2], 2, seed=1)
    states = {
        plan[0]["entry"]: {"status": "done"},
        plan[1]["entry"]: {"status": "running"},
    }
    picked = bench.next_entries(plan, states, 5)
    assert [p["entry"] for p in picked] == [p["entry"] for p in plan[2:]]


def test_the_cluster_id_is_scrubbed():
    assert bench.scrub('{"cluster_id": "abc123"}', "abc123") == (
        '{"cluster_id": "<DSQL_CLUSTER_ID>"}'
    )
    assert bench.scrub("x", "") == "x"
    assert bench.scrub("account 123456789012 abc123", "abc123", "123456789012") == (
        "account <ACCOUNT_ID> <DSQL_CLUSTER_ID>"
    )
