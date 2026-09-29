"""
2026-09 审计修复回归测试 (gitlab_controller / governance_scan_all / evidence_bundle /
run_affected_tests): 每条对应一个会让门禁静默放行或崩溃的缺陷。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import create_mr  # noqa: E402
import evidence_bundle  # noqa: E402
import gitlab_controller  # noqa: E402
import governance_scan_all  # noqa: E402
import run_affected_tests  # noqa: E402


def _submit_args(**overrides) -> SimpleNamespace:
    base = dict(
        gitlab_url="https://gitlab.example.com", gitlab_project_id="group/project",
        gitlab_token="token", target_branch="master", source_branch="feat/x",
        policy_path="governance.config.yml", why="验证 MR", requirement_id=None,
        what=None, tested=None, risks=None, excludes=None, link=None, title=None,
        config=None, evidence=create_mr.EVIDENCE_PATH, meta_style="details",
        remove_source_branch=False, output=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class GitLabControllerSubmitTests(unittest.TestCase):
    """controller 是 AI 创建 MR 的受控通道, 不能绕过 create_mr 的本地门禁。"""

    def _run(self, args, cfg=None, **extra):
        mock.patch.object(gitlab_controller, "build_readiness",
                          return_value={"status": "pass", "checks": []}).start()
        mock.patch.object(create_mr, "load_config", return_value=cfg or {}).start()
        mock.patch.object(create_mr, "infer_title", return_value="title").start()
        for name, value in extra.items():
            mock.patch.object(create_mr, name, value).start()
        self.addCleanup(mock.patch.stopall)
        with redirect_stdout(io.StringIO()):
            return gitlab_controller.submit(args)

    def test_local_preflight_failure_blocks_api_submit(self) -> None:
        api = mock.Mock(return_value=0)
        rc = self._run(
            _submit_args(),
            build_description=mock.Mock(return_value="desc"),
            run_local_preflight=mock.Mock(return_value=1),
            submit_gitlab_api=api,
        )
        self.assertEqual(1, rc)
        api.assert_not_called()

    def test_requirement_id_resolves_background_when_why_missing(self) -> None:
        build = mock.Mock(return_value="desc")
        resolve = mock.Mock(return_value="CR-12")
        rc = self._run(
            _submit_args(why=None, requirement_id="cr-12"),
            cfg={"deliverhq_integration": {"enabled": True}},
            resolve_requirement_id=resolve,
            read_why_from_requirement=mock.Mock(return_value=("需求背景", "ok")),
            build_description=build,
            run_local_preflight=mock.Mock(return_value=0),
            submit_gitlab_api=mock.Mock(return_value=0),
        )
        self.assertEqual(0, rc)
        resolve.assert_called_once_with("cr-12", "master")
        mr_args = build.call_args[0][0]
        self.assertEqual("需求背景", mr_args.why)
        self.assertIn("Requirement-ID: CR-12", mr_args.link)

    def test_system_exit_from_git_still_writes_json_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "result.json"
            rc = self._run(
                _submit_args(output=str(out)),
                build_description=mock.Mock(side_effect=SystemExit(1)),
            )
            self.assertEqual(1, rc)
            self.assertEqual("fail", json.loads(out.read_text(encoding="utf-8"))["status"])


class GitLabControllerPolicyDigestTests(unittest.TestCase):
    """policy 摘要须与 evidence_bundle.file_digest 对同一文件一致, 坏数据失败关闭。"""

    def _digest(self, content: str):
        with mock.patch.object(gitlab_controller, "_api", return_value={"content": content}):
            return gitlab_controller._target_policy(_submit_args())

    def test_digest_matches_raw_bytes_and_rejects_bad_base64(self) -> None:
        raw = b"key: \xff\xfe value\n"
        digest, _ = self._digest(base64.b64encode(raw).decode())
        self.assertEqual("sha256:" + hashlib.sha256(raw).hexdigest(), digest)
        with self.assertRaises(RuntimeError):
            self._digest("@@@@")

class GovernanceScanAllTests(unittest.TestCase):
    """统一扫描入口的退出码就是 CI 门禁, 任何子扫描异常都不能被当作通过。"""

    def _main(self, argv, codes=(0, 0, 0)):
        calls = []

        def fake_run(name, cmd, cwd=None, stdin=None):
            calls.append((name, cmd, stdin))
            return codes[len(calls) - 1], "", ""

        with mock.patch.object(governance_scan_all, "run", side_effect=fake_run), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = governance_scan_all.main(argv)
        return rc, calls

    def test_signal_killed_scan_fails_overall(self) -> None:
        rc, _ = self._main(["--diff-base", "HEAD~1"], codes=(-9, 0, 0))
        self.assertNotEqual(0, rc)

    def test_mr_validate_without_body_file_uses_env_or_devnull_stdin(self) -> None:
        _, calls = self._main(["--diff-base", "HEAD~1"])
        _name, cmd, stdin = calls[2]
        self.assertNotIn("--file", cmd)
        self.assertNotIn("/dev/null", cmd)
        self.assertIs(subprocess.DEVNULL, stdin)

    def test_config_only_forwarded_when_explicit(self) -> None:
        _, calls = self._main(["--diff-base", "HEAD~1"])
        self.assertFalse(any("--config" in cmd for _n, cmd, _s in calls))
        _, calls = self._main(["--diff-base", "HEAD~1", "--config", "x.yml"])
        self.assertIn("x.yml", calls[0][1])
        self.assertIn("x.yml", calls[2][1])

_BINDINGS = ("source_sha", "target_sha", "merge_sha", "policy_digest", "profile_digest")


class EvidenceBundleTests(unittest.TestCase):
    """证据绑定校验是合并门禁的依据, 缺失/畸形输入必须失败关闭。"""

    def _bundle(self) -> dict:
        data = {key: f"v-{key}" for key in _BINDINGS}
        data.update(schema_version=evidence_bundle.SCHEMA_VERSION,
                    checks=[{"id": "unit", "status": "pass"}])
        return data

    def test_single_empty_expectation_fails_closed(self) -> None:
        expected = {key: f"v-{key}" for key in _BINDINGS}
        expected["profile_digest"] = ""  # 例如 CI 变量未设置
        problems = evidence_bundle.verify_bundle(self._bundle(), expected)
        self.assertIn("profile_digest_expected_missing", problems)

    def test_changed_paths_use_explicit_shas(self) -> None:
        args = SimpleNamespace(
            repository="r", profile=str(ROOT / "profiles" / "flutter-mobile.yml"),
            policy=str(ROOT / "governance.config.yml"), risk="medium",
            source_ref="HEAD", target_ref="origin/main", source_sha="src-sha",
            target_sha="tgt-sha", merge_sha="m", create_synthetic_merge=False,
            include_changed_paths=True, policy_digest=None,
        )
        with mock.patch.object(evidence_bundle, "changed_paths", return_value=[]) as cp:
            evidence_bundle.build_plan(args)
        cp.assert_called_once_with("tgt-sha", "src-sha")

    def test_non_object_json_fails_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            path.write_text("[1, 2]", encoding="utf-8")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                bundle_rc = evidence_bundle.cmd_bundle(SimpleNamespace(checks=str(path), output=None))
                verify_rc = evidence_bundle.cmd_verify(SimpleNamespace(
                    bundle=str(path), **{key: "v" for key in _BINDINGS}))
        self.assertEqual(2, bundle_rc)
        self.assertEqual(1, verify_rc)

class RunAffectedTestsFixes(unittest.TestCase):
    """受影响包推导错误会导致漏测 (放行) 或误报 (阻断)。"""

    def test_go_list_braces_inside_strings_do_not_drop_packages(self) -> None:
        pkgs = [
            {"ImportPath": "ex.com/app/a", "Doc": "func() { unclosed", "Imports": []},
            {"ImportPath": "ex.com/app/b", "Imports": ["ex.com/app/a"]},
        ]
        out = "\n".join(json.dumps(p, indent="\t") for p in pkgs)
        done = mock.Mock(returncode=0, stdout=out, stderr="")
        with mock.patch.object(run_affected_tests.subprocess, "run", return_value=done):
            reverse = run_affected_tests.build_reverse_dep_map("/m")
        self.assertEqual({"ex.com/app/b"}, reverse.get("ex.com/app/a"))

    def test_paths_rebased_onto_submodule_and_deleted_packages_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as repo:
            module = os.path.join(repo, "svc")
            os.makedirs(os.path.join(module, "pkg", "db"))
            rebased = run_affected_tests.rebase_to_module(
                ["svc/pkg/db", "svc/pkg/gone", "tools/x"], module, repo)
            self.assertEqual(["pkg/db", "pkg/gone"], rebased)
            self.assertEqual(["pkg/db"], run_affected_tests.existing_packages(rebased, module))

    def test_git_disables_quotepath_for_non_ascii_paths(self) -> None:
        done = mock.Mock(stdout="")
        with mock.patch.object(run_affected_tests.subprocess, "run", return_value=done) as run:
            run_affected_tests.run_git(["diff", "--name-only"])
        cmd = run.call_args[0][0]
        self.assertEqual(["git", "-c", "core.quotepath=off"], cmd[:3])


if __name__ == "__main__":
    unittest.main()
