"""The primitives a scenario is made of, and how each one is undone.

Every fault travels a real path:

- `set_env` changes one environment variable, publishes a new version and
  moves the live alias to it: exactly what a configuration deploy does.
- `deploy_patch` builds the service's zip the way Terraform does, with one
  small code change, publishes it and moves the alias: a bad deploy.
- `send_message` puts one message on a queue: a producer bug.

Each alias move is recorded in the deployments table like any other deploy,
because that is what the investigator would see after a real one.

Publishing a version always snapshots $LATEST, which Terraform manages. So
every injection first saves what it is about to change, and `restore` puts
it back byte for byte: the original environment, or Terraform's own zip from
terraform/.build/. A run is only recovered when `terraform plan` is clean.

With `dry_run`, every AWS write is printed instead of made.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import deployments

BUILD = REPO_ROOT / "terraform" / ".build"
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def code_sha256(data: bytes) -> str:
    """The form Lambda reports CodeSha256 in: base64 of the raw digest."""
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


def build_zip(service: str, replace: dict[str, str] | None = None) -> bytes:
    """The service's artifact, laid out like the lambda_service module's.

    Service files at the root, shared code under common/. `replace` maps
    {"file": ..., "find": ..., "with": ...} for one exact, single occurrence
    substitution, which is how a bad deploy's code change is described.
    """
    files: dict[str, str] = {}
    for path in sorted((REPO_ROOT / "src" / service).rglob("*.py")):
        files[str(path.relative_to(REPO_ROOT / "src" / service))] = path.read_text()
    for path in sorted((REPO_ROOT / "src" / "common").rglob("*.py")):
        files["common/" + str(path.relative_to(REPO_ROOT / "src" / "common"))] = (
            path.read_text()
        )

    if replace:
        text = files[replace["file"]]
        if text.count(replace["find"]) != 1:
            raise ValueError(
                f"{replace['find']!r} must occur exactly once in {replace['file']}"
            )
        files[replace["file"]] = text.replace(replace["find"], replace["with"])

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, ZIP_EPOCH)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, files[name])
    return out.getvalue()


class Injector:
    """Makes the changes, remembers them, and undoes them in reverse."""

    def __init__(
        self,
        lambda_client: Any,
        sqs: Any,
        table: Any,
        *,
        actor: str,
        git_sha: str | None,
        dry_run: bool,
        state_path: Path | None = None,
    ) -> None:
        self.lam = lambda_client
        self.sqs = sqs
        self.table = table
        self.actor = actor
        self.git_sha = git_sha
        self.dry_run = dry_run
        self.state_path = state_path
        self.injections: list[dict[str, Any]] = []

    # -- bookkeeping ---------------------------------------------------------

    def _save(self) -> None:
        """Written after every change, so a crash mid-run can still be undone."""
        if self.state_path and not self.dry_run:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self.injections, indent=2))

    def _write(self, description: str, call, *args, **kwargs):
        print(("  WOULD " if self.dry_run else "  ") + description, flush=True)
        if self.dry_run:
            return None
        return call(*args, **kwargs)

    def _wait_updated(self, function: str) -> None:
        if not self.dry_run:
            self.lam.get_waiter("function_updated_v2").wait(FunctionName=function)

    def _publish_and_move(
        self, service: str, what: str, record_deploy: bool = True
    ) -> tuple[str, str]:
        function = deployments.function_name(service)
        previous = deployments.current_version(self.lam, service)
        published = self._write(
            f"publish a new version of {function}",
            self.lam.publish_version,
            FunctionName=function,
        )
        new = published["Version"] if published else "NEW"
        self._write(
            f"move {function}:live {previous} -> {new}",
            deployments.move_alias,
            self.lam,
            service,
            new,
        )
        if not record_deploy:
            # A change to something we do not own, standing in for a third
            # party: a real provider's slowdown leaves no row in our table.
            return previous, new
        self._write(
            f"record the {what} in the deployments table",
            deployments.record,
            self.table,
            service=service,
            previous=previous,
            new=new,
            kind="deploy",
            actor=self.actor,
            git_sha=self.git_sha,
        )
        return previous, new

    # -- faults --------------------------------------------------------------

    def set_env(
        self, service: str, name: str, value: str, record_deploy: bool = True
    ) -> None:
        function = deployments.function_name(service)
        config = self.lam.get_function_configuration(FunctionName=function)
        original = dict(config.get("Environment", {}).get("Variables", {}))
        if name not in original:
            raise ValueError(f"{function} has no environment variable {name}")
        changed = {**original, name: value}
        record: dict[str, Any] = {
            "kind": "env",
            "service": service,
            "original_env": original,
            "recorded": record_deploy,
        }
        self.injections.append(record)
        self._save()
        self._write(
            f"set {name} on {function} $LATEST",
            self.lam.update_function_configuration,
            FunctionName=function,
            Environment={"Variables": changed},
        )
        self._wait_updated(function)
        record["previous"], record["new"] = self._publish_and_move(
            service, "configuration change", record_deploy
        )
        self._save()

    def deploy_patch(self, service: str, file: str, find: str, with_: str) -> None:
        function = deployments.function_name(service)
        patched = build_zip(service, {"file": file, "find": find, "with": with_})
        record: dict[str, Any] = {"kind": "code", "service": service}
        self.injections.append(record)
        self._save()
        self._write(
            f"upload changed code to {function} $LATEST",
            self.lam.update_function_code,
            FunctionName=function,
            ZipFile=patched,
        )
        self._wait_updated(function)
        record["previous"], record["new"] = self._publish_and_move(service, "deploy")
        self._save()

    def send_message(self, queue: str, body: str) -> None:
        url = self.sqs.get_queue_url(QueueName=queue)["QueueUrl"]
        self.injections.append({"kind": "message", "queue": queue, "body": body})
        self._save()
        self._write(
            f"send one message to {queue}",
            self.sqs.send_message,
            QueueUrl=url,
            MessageBody=body,
        )

    # -- recovery ------------------------------------------------------------

    def restore(self, reason: str) -> None:
        """Undo every injection, newest first. Messages are handled by
        drain_dlq_message, since a delivered message cannot be unsent."""
        for record in reversed(self.injections):
            if record["kind"] not in ("env", "code"):
                continue
            service = record["service"]
            function = deployments.function_name(service)
            if "new" in record:
                self._write(
                    f"roll {function}:live back {record['new']} -> {record['previous']}",
                    deployments.move_alias,
                    self.lam,
                    service,
                    record["previous"],
                )
                # Only undo a row that was written: a change standing in for
                # a third party left no deploy row, so it gets no rollback row.
                if record.get("recorded", True):
                    self._write(
                        "record the rollback",
                        deployments.record,
                        self.table,
                        service=service,
                        previous=record["new"],
                        new=record["previous"],
                        kind="rollback",
                        actor=self.actor,
                        reason=reason,
                    )
            if record["kind"] == "env":
                self._write(
                    f"restore {function} $LATEST environment",
                    self.lam.update_function_configuration,
                    FunctionName=function,
                    Environment={"Variables": record["original_env"]},
                )
            else:
                original = (BUILD / f"{function}.zip").read_bytes()
                self._write(
                    f"restore {function} $LATEST code from terraform/.build",
                    self.lam.update_function_code,
                    FunctionName=function,
                    ZipFile=original,
                )
            self._wait_updated(function)
            record["restored"] = True
            self._save()

    def drain_dlq_message(self, dlq: str) -> int:
        """Delete only the messages this run sent, from the DLQ."""
        bodies = {r["body"] for r in self.injections if r["kind"] == "message"}
        if not bodies:
            return 0
        url = self.sqs.get_queue_url(QueueName=dlq)["QueueUrl"]
        deleted = 0
        if self.dry_run:
            print(f"  WOULD delete this run's message(s) from {dlq}")
            return 0
        for _ in range(10):
            messages = self.sqs.receive_message(
                QueueUrl=url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=2,
            ).get("Messages", [])
            for message in messages:
                if message["Body"] in bodies:
                    self.sqs.delete_message(
                        QueueUrl=url, ReceiptHandle=message["ReceiptHandle"]
                    )
                    deleted += 1
            if deleted >= len(bodies):
                break
        print(f"  deleted {deleted} message(s) from {dlq}")
        return deleted
