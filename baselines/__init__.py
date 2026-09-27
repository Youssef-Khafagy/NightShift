"""The two baselines the agent is measured against (M7).

- runbook: a scripted if/else runbook. The same read-only tools, no model.
- alarm_only: the same model as the agent, shown only the alarm.

Both produce the same InvestigationState and Report as the agent, so the
grader and the postmortem treat all three alike. Like the agent, they may
never read the chaos package or the grader (tests/test_integrity.py).
"""
