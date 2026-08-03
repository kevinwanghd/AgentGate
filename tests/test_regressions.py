from __future__ import annotations

import datetime as dt
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

check_tested = importlib.import_module("check_tested")
create_mr = importlib.import_module("create_mr")
evidence_bundle = importlib.import_module("evidence_bundle")
agentgate = importlib.import_module("agentgate")
gitlab_mr_compat = importlib.import_module("gitlab_mr_compat")
risk_merge_decision = importlib.import_module("risk_merge_decision")
scan_risks = importlib.import_module("scan_risks")
validate_mr = importlib.import_module("validate_mr")
gate_decision = importlib.import_module("gate_decision")
gitlab_controller = importlib.import_module("gitlab_controller")


class ConfigFailureTests(unittest.TestCase):
    def test_explicit_missing_config_is_fatal(self) -> None:
        with self.assertRaises(Exception):
            scan_risks.load_config("definitely-missing.yml")

    def test_invalid_config_is_fatal(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
            f.write("risk_annotations: [not-a-mapping]\n")
            path = f.name
        try:
            with self.assertRaises(Exception):
                scan_risks.load_config(path)
        finally:
            os.unlink(path)

    def test_invalid_enforcement_is_rejected(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
            f.write("metadata:\n  enforcement: maybe\n")
            path = f.name
        try:
            with self.assertRaises(Exception):
                validate_mr.load_config(path)
        finally:
            os.unlink(path)

    def test_invalid_custom_regex_is_fatal_in_hard_mode(self) -> None:
        cfg = json.loads(json.dumps(scan_risks.DEFAULT_CONFIG))
        cfg["risk_annotations"]["enforcement"] = "hard"
        cfg["risk_annotations"]["custom_patterns"] = [
            {"type": "broken", "regex": "(", "desc": "broken rule"}
        ]
        with self.assertRaises(scan_risks.ConfigError):
            scan_risks.build_custom_patterns(cfg)

    def test_invalid_custom_regex_returns_config_exit_code(self) -> None:
        cfg = json.loads(json.dumps(scan_risks.DEFAULT_CONFIG))
        cfg["risk_annotations"]["enforcement"] = "hard"
        cfg["risk_annotations"]["custom_patterns"] = [
            {"type": "broken", "regex": "(", "desc": "broken rule"}
        ]
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
            f.write("+++ b/app.py\n@@ -0,0 +1 @@\n+print('x')\n")
            diff_path = f.name
        try:
            with mock.patch.object(scan_risks, "load_config", return_value=cfg), \
                    mock.patch.object(sys, "argv", ["scan_risks.py", "--diff-file", diff_path]):
                self.assertEqual(2, scan_risks.main())
        finally:
            os.unlink(diff_path)

    def test_invalid_custom_regex_is_skipped_in_soft_mode(self) -> None:
        cfg = json.loads(json.dumps(scan_risks.DEFAULT_CONFIG))
        cfg["risk_annotations"]["enforcement"] = "soft"
        cfg["risk_annotations"]["custom_patterns"] = [
            {"type": "broken", "regex": "(", "desc": "broken rule"}
        ]
        self.assertEqual([], scan_risks.build_custom_patterns(cfg))


class RiskAnnotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = json.loads(json.dumps(scan_risks.DEFAULT_CONFIG))
        self.cfg["risk_annotations"]["enforcement"] = "hard"

    def test_each_risk_on_a_line_requires_its_own_annotation(self) -> None:
        today = dt.date.today().isoformat()
        lines = [
            f'// risk:auth-bypass reason:"approved internal identity comparison" owner:@sec reviewed:{today}',
            # risk:auth-bypass reason:"regression fixture for multiple risk coverage" owner:@sec reviewed:2026-07-11
            # risk:magic-id reason:"fixed object identifier used only by scanner regression" owner:@sec reviewed:2026-07-11
            'if (userId == "626786582b50ab8ec08b0fa0") return true;',
        ]
        ok, problems = scan_risks.find_annotation(
            lines, 2, {"auth-bypass", "magic-id"}, self.cfg
        )
        self.assertFalse(ok)
        self.assertTrue(any("magic-id" in p for p in problems))

    def test_test_removal_rejects_stale_review_date(self) -> None:
        old = (dt.date.today() - dt.timedelta(days=999)).isoformat()
        diff = (
            "-def test_payment():\n"
            f'+# risk:test-removal reason:"obsolete duplicate payment scenario" owner:@qa reviewed:{old}\n'
        )
        problems = scan_risks.check_test_removal(diff, self.cfg)
        self.assertTrue(problems)
        self.assertIn("过期", problems[0])

    def test_each_removed_test_requires_an_annotation(self) -> None:
        today = dt.date.today().isoformat()
        diff = (
            "-def test_payment():\n"
            "-def test_refund():\n"
            f'+# risk:test-removal reason:"duplicate payment scenario is obsolete" '
            f"owner:@qa reviewed:{today}\n"
        )
        problems = scan_risks.check_test_removal(diff, self.cfg)
        self.assertEqual(1, len(problems))
        self.assertIn("只有 1 条", problems[0])

    def test_multiline_empty_catch_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "service.cs"
            source.write_text("catch (Exception)\n{\n}\n", encoding="utf-8")
            diff = (
                f"+++ b/{source.as_posix()}\n"
                "@@ -0,0 +1,3 @@\n"
                "+catch (Exception)\n+{\n+}\n"
            )
            violations = scan_risks.scan(diff, self.cfg)
        self.assertTrue(any(v["type"] == "swallowed-exception" for v in violations))


class EvidenceBindingTests(unittest.TestCase):
    def test_failed_trailer_cannot_be_hidden_by_pass(self) -> None:
        completed = mock.Mock(stdout="Tested: fail\n\nTested: pass (10/10)\n")
        with mock.patch.object(check_tested.subprocess, "run", return_value=completed):
            self.assertEqual("fail", check_tested.read_tested_trailer("main"))

    def test_stale_evidence_does_not_count_as_green(self) -> None:
        evidence = [{
            "cmd": "pytest",
            "failed": 0,
            "git_state": "old-state",
            "covers": ["src/app.py"],
        }]
        effective = check_tested.filter_evidence_for_state(evidence, "new-state")
        self.assertEqual([], effective)

    def test_current_evidence_is_retained(self) -> None:
        evidence = [{"cmd": "pytest", "failed": 0, "git_state": "same"}]
        self.assertEqual(evidence, check_tested.filter_evidence_for_state(evidence, "same"))

    def test_tested_trailer_can_be_disabled_as_release_evidence(self) -> None:
        cfg = json.loads(json.dumps(check_tested.DEFAULT_CONFIG))
        cfg["testing"]["enforcement"] = "hard"
        cfg["testing"]["accept_tested_trailer"] = False
        diff = (
            "+++ b/src/app.py\n"
            "@@ -0,0 +1 @@\n"
            "+print('changed')\n"
        )
        _, violations = check_tested.check(diff, [], cfg, trailer="pass")
        self.assertEqual(["src/app.py"], [v["file"] for v in violations])

    def test_create_mr_ignores_stale_evidence(self) -> None:
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
            f.write(json.dumps({
                "cmd": "pytest", "failed": 0, "exit_code": 0,
                "passed": 4, "total": 4, "git_state": "old",
            }) + "\n")
            path = f.name
        try:
            with mock.patch.object(create_mr, "repository_state", return_value="new"):
                rendered = create_mr.gen_tested(path)
            self.assertNotIn("4/4", rendered)
            self.assertIn("[ ]", rendered)
        finally:
            os.unlink(path)


class CreateMrGitLabApiTests(unittest.TestCase):
    def test_gitlab_preflight_checks_project_access(self) -> None:
        args = mock.Mock(
            gitlab_url="https://gitlab.example.com",
            gitlab_project_id="group/project",
            gitlab_token="token",
        )
        with mock.patch.object(
            create_mr,
            "_gitlab_api_request",
            return_value={"path_with_namespace": "group/project"},
        ) as request:
            rc = create_mr.gitlab_api_preflight(args)

        self.assertEqual(0, rc)
        request.assert_called_once_with(
            "GET",
            "https://gitlab.example.com",
            "token",
            "/projects/group%2Fproject",
        )

    def test_gitlab_api_submit_updates_existing_mr(self) -> None:
        args = mock.Mock(
            gitlab_url="https://gitlab.example.com",
            gitlab_project_id="123",
            gitlab_token="token",
            source_branch=None,
            remove_source_branch=True,
        )

        calls = []

        def fake_request(method, base_url, token, path, payload=None, query=None):
            calls.append((method, base_url, token, path, payload, query))
            if method == "GET" and path.endswith("/merge_requests"):
                return [{"iid": 7}]
            return {
                "iid": 7,
                "web_url": "https://gitlab.example.com/group/project/-/merge_requests/7",
            }

        with mock.patch.object(create_mr, "current_branch", return_value="feature/a"), \
                mock.patch.object(create_mr, "_gitlab_api_request", side_effect=fake_request):
            rc = create_mr.submit_gitlab_api("title", "body", "master", args)

        self.assertEqual(0, rc)
        self.assertEqual("GET", calls[0][0])
        self.assertEqual(
            {
                "state": "opened",
                "source_branch": "feature/a",
                "target_branch": "master",
            },
            calls[0][5],
        )
        self.assertEqual("PUT", calls[1][0])
        self.assertEqual("/projects/123/merge_requests/7", calls[1][3])
        # update_payload intentionally omits target_branch and remove_source_branch:
        # GitLab 11.4 returns 500 when these fields are included in a PUT request.
        self.assertNotIn("remove_source_branch", calls[1][4])
        self.assertNotIn("target_branch", calls[1][4])
        self.assertEqual("title", calls[1][4]["title"])
        self.assertEqual("body", calls[1][4]["description"])


class AgentGateCliTests(unittest.TestCase):
    def test_mr_create_delegates_to_create_mr(self) -> None:
        captured = []

        def delegate() -> int:
            captured.extend(sys.argv[1:])
            return 0

        original_argv = list(sys.argv)
        with mock.patch.object(create_mr, "main", side_effect=delegate) as delegated:
            rc = agentgate.main(["mr", "create", "--why", "修复广告生命周期问题"])

        self.assertEqual(0, rc)
        delegated.assert_called_once()
        self.assertEqual(["--why", "修复广告生命周期问题"], captured)
        self.assertEqual(original_argv, sys.argv)

    def test_mr_prepare_is_a_first_class_command(self) -> None:
        captured = []

        def delegate() -> int:
            captured.extend(sys.argv[1:])
            return 0

        original_argv = list(sys.argv)
        with mock.patch.object(create_mr, "main", side_effect=delegate):
            rc = agentgate.main(["mr", "prepare", "--why", "修复门禁"])

        self.assertEqual(0, rc)
        self.assertEqual(["--prepare", "--why", "修复门禁"], captured)
        self.assertEqual(original_argv, sys.argv)

    def test_mr_verify_is_a_first_class_command(self) -> None:
        captured = []

        def delegate() -> int:
            captured.extend(sys.argv[1:])
            return 0

        original_argv = list(sys.argv)
        with mock.patch.object(create_mr, "main", side_effect=delegate):
            rc = agentgate.main(["mr", "verify", "--target-branch", "origin/main"])

        self.assertEqual(0, rc)
        self.assertEqual(["--verify-manifest", "--target-branch", "origin/main"], captured)
        self.assertEqual(original_argv, sys.argv)

    def test_pr_verify_alias_is_a_first_class_command(self) -> None:
        captured = []

        def delegate() -> int:
            captured.extend(sys.argv[1:])
            return 0

        with mock.patch.object(create_mr, "main", side_effect=delegate):
            rc = agentgate.main(["pr", "verify", "--target-branch", "origin/main"])

        self.assertEqual(0, rc)
        self.assertEqual(["--verify-manifest", "--target-branch", "origin/main"], captured)

    def test_create_mr_rejects_invalid_generated_description(self) -> None:
        args = mock.Mock(config=None, target_branch="origin/master")
        with mock.patch.object(validate_mr, "load_config", return_value={}), \
                mock.patch.object(validate_mr, "validate", return_value=["缺少 ## 背景 段落"]):
            rc = create_mr.validate_generated_description("Bug", args)

        self.assertEqual(1, rc)

    def test_create_mr_preflight_runs_description_risk_and_tests(self) -> None:
        args = mock.Mock(
            config="governance.config.yml",
            target_branch="origin/main",
            skip_local_validate=False,
            skip_risk_scan=False,
            skip_tests=False,
            preflight_test_command=None,
        )
        cfg = {"create_mr": {"preflight_test_command": "python -m unittest tests.test_regressions.AgentGateCliTests"}}
        calls = []

        def fake_run(cmd, text=True):
            calls.append(cmd)
            return mock.Mock(returncode=0)

        with mock.patch.object(create_mr, "validate_generated_description", return_value=0) as validate, \
                mock.patch.object(create_mr.subprocess, "run", side_effect=fake_run):
            rc = create_mr.run_local_preflight("## 背景\n\n修复门禁。", args, cfg)

        self.assertEqual(0, rc)
        validate.assert_called_once()
        self.assertEqual(sys.executable, calls[0][0])
        self.assertTrue(calls[0][1].endswith("scan_risks.py"))
        self.assertEqual(
            [sys.executable, "-m", "unittest", "tests.test_regressions.AgentGateCliTests"],
            calls[1],
        )

    def test_create_mr_preflight_stops_before_tests_when_risk_scan_fails(self) -> None:
        args = mock.Mock(
            config=None,
            target_branch="origin/main",
            skip_local_validate=False,
            skip_risk_scan=False,
            skip_tests=False,
            preflight_test_command=None,
        )
        cfg = {"create_mr": {"preflight_test_command": "python -m unittest tests.test_regressions.AgentGateCliTests"}}

        with mock.patch.object(create_mr, "validate_generated_description", return_value=0), \
                mock.patch.object(create_mr.subprocess, "run", return_value=mock.Mock(returncode=1)) as run:
            rc = create_mr.run_local_preflight("## 背景\n\n修复门禁。", args, cfg)

        self.assertEqual(1, rc)
        self.assertEqual(1, run.call_count)


class GitLabMrCompatTests(unittest.TestCase):
    def test_derives_gitlab_url_from_legacy_ci_variables(self) -> None:
        args = mock.Mock(gitlab_url=None)
        with mock.patch.dict(os.environ, {"CI_API_V4_URL": "https://gitlab.example.com/api/v4"}, clear=True):
            self.assertEqual(
                "https://gitlab.example.com",
                gitlab_mr_compat._derive_gitlab_url(args),
            )

        with mock.patch.dict(os.environ, {"CI_API_V4_URL": "", "CI_PROJECT_URL": "https://gitlab.example.com/group/project"}, clear=True):
            self.assertEqual(
                "https://gitlab.example.com",
                gitlab_mr_compat._derive_gitlab_url(args),
            )

    def test_mr_pipeline_prefers_actual_description(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "result.json"
            env = {
                "CI_COMMIT_REF_NAME": "fix/bug",
                "CI_MERGE_REQUEST_DESCRIPTION": "真实描述",
            }
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(
                        gitlab_mr_compat,
                        "validate_description",
                        return_value=[],
                    ) as validate, \
                    mock.patch.object(gitlab_mr_compat, "_find_open_mr") as api, \
                    mock.patch.object(sys, "argv", [
                        "gitlab_mr_compat.py",
                        "--target-branch", "master",
                        "--output", str(output),
                    ]):
                rc = gitlab_mr_compat.main()

            self.assertEqual(0, rc)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("pass", payload["status"])
            self.assertEqual("gitlab-ci", payload["source"])
            self.assertTrue(payload["actual_mr_verified"])
            self.assertIn("description_sha256", payload)
            validate.assert_called_once()
            api.assert_not_called()

    def test_empty_actual_mr_description_does_not_fall_back(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "result.json"
            manifest = Path(td) / "mr-description.md"
            manifest.write_text("合规清单", encoding="utf-8")
            env = {
                "CI_COMMIT_REF_NAME": "fix/bug",
                "CI_MERGE_REQUEST_DESCRIPTION": "",
            }
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(
                        gitlab_mr_compat,
                        "validate_description",
                        return_value=["缺少 ## 背景 段落"],
                    ) as validate, \
                    mock.patch.object(gitlab_mr_compat, "_manifest_changed") as changed, \
                    mock.patch.object(sys, "argv", [
                        "gitlab_mr_compat.py",
                        "--target-branch", "master",
                        "--manifest-path", str(manifest),
                        "--output", str(output),
                    ]):
                rc = gitlab_mr_compat.main()

            self.assertEqual(1, rc)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("gitlab-ci", payload["source"])
            self.assertTrue(payload["actual_mr_verified"])
            validate.assert_called_once_with("", None, None)
            changed.assert_not_called()

    # risk:test-removal reason:"replaced by explicit read-only API fallback coverage" owner:@agentgate reviewed:2026-07-29
    def test_explicit_api_fallback_uses_dedicated_read_token(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "result.json"
            missing_manifest = Path(td) / "missing-mr-description.md"
            mr = {
                "iid": 25,
                "description": "Bug",
                "web_url": "https://gitlab.example.com/group/project/-/merge_requests/25",
                "target_branch": "master",
            }
            env = {
                "CI_COMMIT_REF_NAME": "fix/bug",
                "CI_SERVER_URL": "https://gitlab.example.com",
                "CI_PROJECT_ID": "123",
                "AGENTGATE_GITLAB_READ_TOKEN": "read-token",
            }
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(create_mr, "_gitlab_api_request", return_value=[mr]) as api, \
                    mock.patch.object(gitlab_mr_compat, "validate_description", return_value=["缺少 ## 背景 段落"]), \
                    mock.patch.object(sys, "argv", [
                        "gitlab_mr_compat.py",
                        "--allow-api-fallback",
                        "--target-branch", "master",
                        "--diff-base", "origin/master",
                        "--manifest-path", str(missing_manifest),
                        "--output", str(output),
                    ]):
                rc = gitlab_mr_compat.main()

            self.assertEqual(1, rc)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("fail", payload["status"])
            self.assertEqual(25, payload["iid"])
            self.assertEqual("gitlab-api", payload["source"])
            self.assertTrue(payload["actual_mr_verified"])
            api.assert_called_once()

    def test_branch_pipeline_uses_current_manifest_without_any_api_call(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "result.json"
            manifest = Path(td) / "mr-description.md"
            manifest.write_text(
                "## 背景\n\n修复旧版流水线无法读取合并请求描述的问题。\n\n"
                "## 变更内容\n\n增加仓库内描述清单作为门禁输入。\n\n"
                "## 自测确认\n\n已运行回归测试并确认全部通过。\n",
                encoding="utf-8",
            )
            env = {
                "CI_COMMIT_REF_NAME": "fix/bug",
                "GOVERNANCE_MERGE_BOT_TOKEN": "must-not-be-used",
                "PRIVATE_TOKEN": "must-not-be-used",
            }
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(
                        gitlab_mr_compat,
                        "_manifest_changed",
                        return_value=True,
                    ), \
                    mock.patch.object(gitlab_mr_compat, "validate_description", return_value=[]) as validate, \
                    mock.patch.object(gitlab_mr_compat, "_find_open_mr") as api, \
                    mock.patch.object(sys, "argv", [
                        "gitlab_mr_compat.py",
                        "--target-branch", "master",
                        "--diff-base", "origin/master",
                        "--manifest-path", str(manifest),
                        "--output", str(output),
                    ]):
                rc = gitlab_mr_compat.main()

            self.assertEqual(0, rc)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("pass", payload["status"])
            self.assertEqual("repository-manifest", payload["source"])
            self.assertFalse(payload["actual_mr_verified"])
            self.assertEqual(str(manifest).replace("\\", "/"), payload["manifest_path"])
            validate.assert_called_once()
            api.assert_not_called()

    def test_branch_pipeline_rejects_stale_repository_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "result.json"
            manifest = Path(td) / "mr-description.md"
            manifest.write_text("## 背景\n\n旧内容。\n", encoding="utf-8")
            env = {
                "CI_COMMIT_REF_NAME": "fix/bug",
            }
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(gitlab_mr_compat, "_manifest_changed", return_value=False), \
                    mock.patch.object(sys, "argv", [
                        "gitlab_mr_compat.py",
                        "--target-branch", "master",
                        "--diff-base", "origin/master",
                        "--manifest-path", str(manifest),
                        "--output", str(output),
                    ]):
                rc = gitlab_mr_compat.main()

            self.assertEqual(1, rc)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("fail", payload["status"])
            self.assertIn("was not changed", payload["reason"])

    # risk:test-removal reason:"replaced by fail-closed missing manifest coverage" owner:@agentgate reviewed:2026-07-29
    def test_missing_manifest_fails_without_implicit_api_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "result.json"
            missing = Path(td) / "missing.md"
            env = {
                "CI_COMMIT_REF_NAME": "fix/bug",
                "CI_SERVER_URL": "https://gitlab.example.com",
                "CI_PROJECT_ID": "123",
                "GOVERNANCE_MERGE_BOT_TOKEN": "merge-token",
                "PRIVATE_TOKEN": "personal-token",
            }
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(gitlab_mr_compat, "_find_open_mr") as api, \
                    mock.patch.object(sys, "argv", [
                        "gitlab_mr_compat.py",
                        "--target-branch", "master",
                        "--manifest-path", str(missing),
                        "--output", str(output),
                    ]):
                rc = gitlab_mr_compat.main()

            self.assertEqual(1, rc)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("fail", payload["status"])
            self.assertIn("API fallback is disabled", payload["reason"])
            api.assert_not_called()

    # risk:test-removal reason:"replaced obsolete token-source rejection with authorized-token acceptance coverage" owner:@agentgate reviewed:2026-07-29
    def test_api_fallback_accepts_any_available_gitlab_token(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "result.json"
            mr = {
                "iid": 25,
                "description": "## 背景\n\n修复问题。\n",
                "web_url": "https://gitlab.example.com/group/project/merge_requests/25",
                "target_branch": "master",
            }
            env = {
                "CI_COMMIT_REF_NAME": "fix/bug",
                "CI_SERVER_URL": "https://gitlab.example.com",
                "CI_PROJECT_ID": "123",
                "GOVERNANCE_MERGE_BOT_TOKEN": "merge-token",
                "PRIVATE_TOKEN": "personal-token",
            }
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(create_mr, "_gitlab_api_request", return_value=[mr]) as api, \
                    mock.patch.object(gitlab_mr_compat, "validate_description", return_value=[]), \
                    mock.patch.object(sys, "argv", [
                        "gitlab_mr_compat.py",
                        "--allow-api-fallback",
                        "--target-branch", "master",
                        "--manifest-path", str(Path(td) / "missing.md"),
                        "--output", str(output),
                    ]):
                rc = gitlab_mr_compat.main()

            self.assertEqual(0, rc)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("pass", payload["status"])
            self.assertEqual("gitlab-api", payload["source"])
            self.assertTrue(payload["actual_mr_verified"])
            self.assertEqual("merge-token", api.call_args.args[2])

    def test_allow_missing_description_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "result.json"
            env = {
                "CI_COMMIT_REF_NAME": "fix/bug",
            }
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(sys, "argv", [
                        "gitlab_mr_compat.py",
                        "--allow-missing-description",
                        "--target-branch", "master",
                        "--manifest-path", str(Path(td) / "missing.md"),
                        "--output", str(output),
                    ]):
                rc = gitlab_mr_compat.main()

            self.assertEqual(0, rc)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("skip", payload["status"])

    def test_target_branch_pipeline_skips_description_validation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "result.json"
            with mock.patch.dict(
                os.environ,
                {"CI_COMMIT_REF_NAME": "master"},
                clear=True,
            ), mock.patch.object(sys, "argv", [
                "gitlab_mr_compat.py",
                "--target-branch", "master",
                "--output", str(output),
            ]):
                rc = gitlab_mr_compat.main()

            self.assertEqual(0, rc)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("skip", payload["status"])


class CreateMrManifestTests(unittest.TestCase):
    def test_writes_description_manifest_and_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / ".agentgate" / "mr-description.md"
            written = create_mr.write_description_manifest(str(target), "## 背景\n\n修复问题。\n")

            self.assertEqual(target, written)
            self.assertEqual("## 背景\n\n修复问题。\n", target.read_text(encoding="utf-8"))

    def test_bound_manifest_round_trips_binding_and_body(self) -> None:
        binding = {
            "schema_version": create_mr.BINDING_SCHEMA,
            "base_ref": "origin/main",
            "prepared_from_sha": "abc123",
            "changed_paths": ["scripts/create_mr.py"],
            "diff_fingerprint": "abc123",
        }
        rendered = create_mr.add_binding_header("## 背景\n\n修复问题。\n", binding)

        parsed, body = create_mr.parse_binding_header(rendered)

        self.assertEqual(binding, parsed)
        self.assertEqual("## 背景\n\n修复问题。\n", body)

    def test_build_binding_uses_prepared_from_sha_not_final_head_claim(self) -> None:
        with mock.patch.object(create_mr, "run_git", return_value="abc123\n"), \
                mock.patch.object(create_mr, "dirty_paths_except_manifest", return_value=[]), \
                mock.patch.object(
                    create_mr, "changed_paths", return_value=["scripts/create_mr.py"]
                ), \
                mock.patch.object(create_mr, "diff_fingerprint", return_value="same"):
            binding = create_mr.build_binding("origin/main", ".agentgate/mr-description.md")

        self.assertEqual("abc123", binding["prepared_from_sha"])
        self.assertNotIn("head_sha", binding)

    def test_verify_manifest_rejects_missing_binding(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / ".agentgate" / "mr-description.md"
            target.parent.mkdir(parents=True)
            target.write_text("## 背景\n\n修复问题。\n", encoding="utf-8")
            args = mock.Mock(target_branch="origin/main", config=None)

            with mock.patch.object(create_mr, "dirty_paths_except_manifest", return_value=[]):
                rc = create_mr.verify_description_manifest(str(target), args)

        self.assertEqual(1, rc)

    def test_verify_manifest_rejects_stale_diff_binding(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / ".agentgate" / "mr-description.md"
            target.parent.mkdir(parents=True)
            binding = {
                "schema_version": create_mr.BINDING_SCHEMA,
                "base_ref": "origin/main",
                "prepared_from_sha": "abc123",
                "changed_paths": ["scripts/create_mr.py"],
                "diff_fingerprint": "old",
            }
            target.write_text(
                create_mr.add_binding_header("## 背景\n\n修复问题。\n", binding),
                encoding="utf-8",
            )
            args = mock.Mock(target_branch="origin/main", config=None)

            with mock.patch.object(
                create_mr, "dirty_paths_except_manifest", return_value=[]
            ), mock.patch.object(
                create_mr, "changed_paths", return_value=["scripts/create_mr.py"]
            ), mock.patch.object(create_mr, "diff_fingerprint", return_value="new"):
                rc = create_mr.verify_description_manifest(str(target), args)

        self.assertEqual(1, rc)

    def test_verify_manifest_rejects_uncommitted_non_manifest_changes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / ".agentgate" / "mr-description.md"
            target.parent.mkdir(parents=True)
            binding = {
                "schema_version": create_mr.BINDING_SCHEMA,
                "base_ref": "origin/main",
                "prepared_from_sha": "abc123",
                "changed_paths": ["scripts/create_mr.py"],
                "diff_fingerprint": "same",
            }
            target.write_text(
                create_mr.add_binding_header("## 背景\n\n修复问题。\n", binding),
                encoding="utf-8",
            )
            args = mock.Mock(target_branch="origin/main", config=None)

            with mock.patch.object(
                create_mr, "dirty_paths_except_manifest", return_value=["scripts/create_mr.py"]
            ):
                rc = create_mr.verify_description_manifest(str(target), args)

        self.assertEqual(1, rc)

    def test_verify_manifest_accepts_current_binding_and_valid_body(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / ".agentgate" / "mr-description.md"
            target.parent.mkdir(parents=True)
            binding = {
                "schema_version": create_mr.BINDING_SCHEMA,
                "base_ref": "origin/main",
                "prepared_from_sha": "abc123",
                "changed_paths": ["scripts/create_mr.py"],
                "diff_fingerprint": "same",
            }
            target.write_text(
                create_mr.add_binding_header("## 背景\n\n修复问题。\n", binding),
                encoding="utf-8",
            )
            args = mock.Mock(target_branch="origin/main", config=None)

            with mock.patch.object(
                create_mr, "dirty_paths_except_manifest", return_value=[]
            ), mock.patch.object(
                create_mr, "changed_paths", return_value=["scripts/create_mr.py"]
            ), mock.patch.object(
                create_mr, "diff_fingerprint", return_value="same"
            ), mock.patch.object(create_mr, "validate_generated_description", return_value=0):
                rc = create_mr.verify_description_manifest(str(target), args)

        self.assertEqual(0, rc)

    def test_build_binding_rejects_uncommitted_non_manifest_changes(self) -> None:
        with mock.patch.object(
            create_mr, "dirty_paths_except_manifest", return_value=["scripts/create_mr.py"]
        ):
            with self.assertRaises(RuntimeError):
                create_mr.build_binding("origin/main", ".agentgate/mr-description.md")


class CreateMrCliAdapterTests(unittest.TestCase):
    @staticmethod
    def _both_clis(name: str) -> str:
        return f"/tools/{name}"

    def test_github_remote_prefers_gh_when_both_clis_exist(self) -> None:
        with mock.patch.object(
            create_mr.shutil,
            "which",
            side_effect=self._both_clis,
        ):
            cli = create_mr.detect_cli(
                "https://github.com/example/project.git"
            )

        self.assertEqual("gh", cli)

    def test_gitlab_remote_prefers_glab_when_both_clis_exist(self) -> None:
        with mock.patch.object(
            create_mr.shutil,
            "which",
            side_effect=self._both_clis,
        ):
            cli = create_mr.detect_cli(
                "git@gitlab.example.com:group/project.git"
            )

        self.assertEqual("glab", cli)

    def test_unknown_remote_uses_only_available_cli(self) -> None:
        with mock.patch.object(
            create_mr.shutil,
            "which",
            side_effect=lambda name: "/tools/gh" if name == "gh" else None,
        ):
            cli = create_mr.detect_cli("ssh://git@example.com/project.git")

        self.assertEqual("gh", cli)

    def test_glab_submit_does_not_mix_fill_with_explicit_description(self) -> None:
        completed = mock.Mock(returncode=0)
        with mock.patch.object(
            create_mr.subprocess,
            "run",
            return_value=completed,
        ) as run:
            rc = create_mr.submit_mr("title", "description", "main", "glab")

        self.assertEqual(0, rc)
        command = run.call_args.args[0]
        self.assertIn("--description", command)
        self.assertIn("--yes", command)
        self.assertNotIn("--fill", command)


class CreateMrSubmitFallbackTests(unittest.TestCase):
    """测试 _submit_with_fallback 的优先级回退链: explicit API > token env > CLI > print"""

    def _args(self, **kwargs):
        defaults = dict(
            gitlab_api=False,
            gitlab_url=None,
            gitlab_project_id=None,
            gitlab_token=None,
            source_branch=None,
            target_branch="master",
            remove_source_branch=False,
        )
        defaults.update(kwargs)
        return mock.Mock(**defaults)

    def _clear_env(self):
        return {k: "" for k in [
            "AGENTGATE_GITLAB_TOKEN", "AGENTGATE_GITLAB_URL",
            "AGENTGATE_GITLAB_PROJECT_ID", "CI_SERVER_URL", "CI_PROJECT_ID",
            "GITLAB_TOKEN", "GLAB_TOKEN", "PRIVATE_TOKEN",
            "GOVERNANCE_MR_VALIDATE_TOKEN", "GOVERNANCE_MERGE_BOT_TOKEN",
        ]}

    def test_explicit_gitlab_api_flag_skips_auto_detect(self):
        """--gitlab-api 显式指定时直接走 API，不走自动检测"""
        args = self._args(gitlab_api=True)
        with mock.patch.object(create_mr, "submit_gitlab_api", return_value=0) as api, \
                mock.patch.object(create_mr, "detect_cli") as cli_detect:
            rc = create_mr._submit_with_fallback("title", "desc", args)
        self.assertEqual(0, rc)
        api.assert_called_once_with("title", "desc", "master", args)
        cli_detect.assert_not_called()

    def test_auto_api_when_token_env_set(self):
        """有 AGENTGATE_GITLAB_TOKEN + URL + PROJECT_ID 时自动走 API，无需 --gitlab-api"""
        args = self._args()
        env = {
            "AGENTGATE_GITLAB_TOKEN": "tok",
            "AGENTGATE_GITLAB_URL": "https://gitlab.example.com",
            "AGENTGATE_GITLAB_PROJECT_ID": "group/proj",
        }
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(create_mr, "submit_gitlab_api", return_value=0) as api, \
                mock.patch.object(create_mr, "detect_cli") as cli_detect:
            rc = create_mr._submit_with_fallback("title", "desc", args)
        self.assertEqual(0, rc)
        api.assert_called_once()
        cli_detect.assert_not_called()

    def test_auto_api_accepts_common_gitlab_token_env_names(self):
        args = self._args()
        env = {
            "AGENTGATE_GITLAB_TOKEN": "",
            "GITLAB_TOKEN": "gitlab-token",
            "AGENTGATE_GITLAB_URL": "https://gitlab.example.com",
            "AGENTGATE_GITLAB_PROJECT_ID": "group/proj",
        }
        with mock.patch.dict(os.environ, {**self._clear_env(), **env}, clear=False), \
                mock.patch.object(create_mr, "submit_gitlab_api", return_value=0) as api, \
                mock.patch.object(create_mr, "detect_cli") as cli_detect:
            self.assertEqual("gitlab-token", create_mr._gitlab_token_from_env())
            rc = create_mr._submit_with_fallback("title", "desc", args)

        self.assertEqual(0, rc)
        api.assert_called_once()
        cli_detect.assert_not_called()

    def test_no_api_without_token(self):
        """无 token 时不走 API，继续向下回退"""
        args = self._args()
        with mock.patch.dict(os.environ, self._clear_env(), clear=False), \
                mock.patch.object(create_mr, "submit_gitlab_api") as api, \
                mock.patch.object(create_mr, "detect_cli", return_value="glab"), \
                mock.patch.object(create_mr, "submit_mr", return_value=0):
            rc = create_mr._submit_with_fallback("title", "desc", args)
        api.assert_not_called()
        self.assertEqual(0, rc)

    def test_falls_back_to_cli_when_no_token(self):
        """无 token 但有 glab CLI 时走 CLI"""
        args = self._args()
        with mock.patch.dict(os.environ, self._clear_env(), clear=False), \
                mock.patch.object(create_mr, "detect_cli", return_value="glab"), \
                mock.patch.object(create_mr, "submit_mr", return_value=0) as cli_submit:
            rc = create_mr._submit_with_fallback("title", "desc", args)
        self.assertEqual(0, rc)
        cli_submit.assert_called_once_with("title", "desc", "master", "glab")

    def test_falls_back_to_print_when_no_token_no_cli(self):
        """No token or CLI falls back to the browser MR page helper."""
        args = self._args()
        with mock.patch.dict(os.environ, self._clear_env(), clear=False), \
                mock.patch.object(create_mr, "detect_cli", return_value=None), \
                mock.patch.object(create_mr, "open_gitlab_mr_fallback", return_value=1) as fallback:
            rc = create_mr._submit_with_fallback("title", "desc", args)
        self.assertEqual(1, rc)
        fallback.assert_called_once()

    def test_fallback_print_includes_mr_url_when_gitlab_url_known(self):
        """Fallback opens a GitLab MR page with source, target, title, and body prefilled."""
        args = self._args()
        env = {
            "AGENTGATE_GITLAB_TOKEN": "",
            "AGENTGATE_GITLAB_URL": "https://gitlab.example.com",
            "AGENTGATE_GITLAB_PROJECT_ID": "group/proj",
            "CI_SERVER_URL": "", "CI_PROJECT_ID": "",
        }
        stderr_lines = []
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(create_mr, "detect_cli", return_value=None), \
                mock.patch.object(create_mr, "current_branch", return_value="feat/x"), \
                mock.patch.object(create_mr.webbrowser, "open", return_value=True) as browser, \
                mock.patch("sys.stderr", mock.Mock(write=lambda s: stderr_lines.append(s))):
            rc = create_mr._submit_with_fallback("title", "desc", args)
        full = "".join(stderr_lines)
        self.assertIn("https://gitlab.example.com", full)
        self.assertIn("merge_requests/new", full)
        browser.assert_called_once()
        parsed = create_mr.urllib.parse.urlparse(browser.call_args.args[0])
        query = create_mr.urllib.parse.parse_qs(parsed.query)
        self.assertEqual(["feat/x"], query["merge_request[source_branch]"])
        self.assertEqual(["master"], query["merge_request[target_branch]"])
        self.assertEqual(["title"], query["merge_request[title]"])
        self.assertEqual(["desc"], query["merge_request[description]"])
        self.assertEqual(1, rc)

    def test_cli_failure_falls_back_to_prefilled_browser_url(self):
        args = self._args(
            gitlab_url="https://gitlab.example.com",
            gitlab_project_id="group%2Fproj",
            source_branch="fix/x",
        )
        body = "## 背景\n\n修复广告生命周期问题"
        with mock.patch.dict(os.environ, self._clear_env(), clear=False), \
                mock.patch.object(create_mr, "detect_cli", return_value="glab"), \
                mock.patch.object(create_mr, "submit_mr", return_value=1), \
                mock.patch.object(create_mr.webbrowser, "open", return_value=True) as browser:
            rc = create_mr._submit_with_fallback("fix: title", body, args)

        self.assertEqual(1, rc)
        url = browser.call_args.args[0]
        self.assertIn("/group/proj/merge_requests/new?", url)
        query = create_mr.urllib.parse.parse_qs(create_mr.urllib.parse.urlparse(url).query)
        self.assertEqual(["fix: title"], query["merge_request[title]"])
        self.assertEqual([body], query["merge_request[description]"])

    def test_token_in_args_takes_precedence_over_env(self):
        """args.gitlab_token 优先于环境变量 AGENTGATE_GITLAB_TOKEN"""
        args = self._args(
            gitlab_token="args-token",
            gitlab_url="https://gitlab.example.com",
            gitlab_project_id="group/proj",
        )
        env = {"AGENTGATE_GITLAB_TOKEN": "env-token"}
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(create_mr, "submit_gitlab_api", return_value=0) as api:
            rc = create_mr._submit_with_fallback("title", "desc", args)
        self.assertEqual(0, rc)
        # 验证调用的 args 里 gitlab_token 来自 args 而非 env
        call_args = api.call_args[0]
        self.assertEqual("args-token", call_args[3].gitlab_token)


class GitLabControllerTests(unittest.TestCase):
    def _args(self) -> mock.Mock:
        return mock.Mock(
            gitlab_url="https://gitlab.example.com",
            gitlab_project_id="group/project",
            gitlab_token="token",
            target_branch="master",
            source_branch=None,
            policy_path="governance.config.yml",
        )

    def test_p0_readiness_passes_with_bot_protection_and_target_policy(self) -> None:
        def fake_api(method, args, path, payload=None, query=None):
            if path == "/projects/group%2Fproject":
                return {"path_with_namespace": "group/project"}
            if path == "/user":
                return {"username": "agentgate-bot"}
            if path.endswith("/repository/branches/master"):
                return {"commit": {"id": "target-sha"}}
            if path.endswith("/protected_branches/master"):
                return {"push_access_levels": [{"access_level": 0}]}
            if path.endswith("/repository/files/governance.config.yml"):
                import base64
                return {
                    "content": base64.b64encode(b"auto_merge:\n  enabled: true\n").decode()
                }
            raise AssertionError(path)

        with mock.patch.object(gitlab_controller, "_api", side_effect=fake_api):
            result = gitlab_controller.build_readiness(self._args())

        self.assertEqual("pass", result["status"])
        self.assertEqual("target_branch", result["policy_source"])
        self.assertEqual("target-sha", result["target_sha"])
        self.assertTrue(result["policy_digest"].startswith("sha256:"))
        self.assertTrue(all(item["status"] == "pass" for item in result["checks"]))

    def test_p0_readiness_fails_when_target_branch_is_not_protected(self) -> None:
        def fake_api(method, args, path, payload=None, query=None):
            if path == "/projects/group%2Fproject":
                return {"path_with_namespace": "group/project"}
            if path == "/user":
                return {"username": "agentgate-bot"}
            if path.endswith("/repository/branches/master"):
                return {"commit": {"id": "target-sha"}}
            if path.endswith("/protected_branches/master"):
                raise RuntimeError("GitLab API GET protected branch 返回 404")
            if path.endswith("/repository/files/governance.config.yml"):
                import base64
                return {"content": base64.b64encode(b"version: v1\n").decode()}
            raise AssertionError(path)

        with mock.patch.object(gitlab_controller, "_api", side_effect=fake_api):
            result = gitlab_controller.build_readiness(self._args())

        self.assertEqual("fail", result["status"])
        failed = [item for item in result["checks"] if item["status"] == "fail"]
        self.assertEqual(["target_branch_protected"], [item["name"] for item in failed])

    def test_submit_stops_before_mr_when_p0_readiness_fails(self) -> None:
        args = self._args()
        args.why = "验证 MR"
        args.requirement_id = None
        args.what = None
        args.tested = None
        args.risks = None
        args.excludes = None
        args.link = None
        args.title = None
        args.config = None
        args.evidence = create_mr.EVIDENCE_PATH
        args.meta_style = "details"
        args.remove_source_branch = True
        args.output = None

        with mock.patch.object(
            gitlab_controller,
            "build_readiness",
            return_value={"status": "fail", "checks": []},
        ), mock.patch.object(create_mr, "submit_gitlab_api") as submit_api:
            rc = gitlab_controller.submit(args)

        self.assertEqual(1, rc)
        submit_api.assert_not_called()


class EvidenceBundleTests(unittest.TestCase):
    def test_plan_uses_flutter_profile_and_binds_policy_profile_digests(self) -> None:
        profile = ROOT / "profiles" / "flutter-mobile.yml"
        policy = ROOT / "governance.config.yml"
        args = mock.Mock(
            repository="group/zhuishu-flutter",
            profile=str(profile),
            policy=str(policy),
            risk="medium",
            source_ref="HEAD",
            target_ref="origin/main",
            source_sha="source-sha",
            target_sha="target-sha",
            merge_sha="merge-sha",
            create_synthetic_merge=False,
            include_changed_paths=False,
            policy_digest=None,
        )

        plan = evidence_bundle.build_plan(args)

        self.assertEqual("agentgate.io/evidence-plan/v1", plan["schema_version"])
        self.assertEqual("source-sha", plan["source_sha"])
        self.assertEqual("target-sha", plan["target_sha"])
        self.assertEqual("merge-sha", plan["merge_sha"])
        self.assertTrue(plan["policy_digest"].startswith("sha256:"))
        self.assertTrue(plan["profile_digest"].startswith("sha256:"))
        self.assertEqual(
            ["dart-format", "flutter-analyze", "flutter-test", "secret-scan"],
            [item["id"] for item in plan["checks"]],
        )

    def test_bundle_normalizes_check_mapping_and_verifies_bindings(self) -> None:
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as checks:
            checks.write('{"checks":{"flutter-test":"pass","flutter-analyze":"pass"}}')
            checks_path = checks.name
        try:
            args = mock.Mock(
                execution_id="ag-exec-1",
                repository="group/zhuishu-flutter",
                source_sha="source",
                target_sha="target",
                merge_sha="merge",
                policy_digest="sha256:policy",
                profile_digest="sha256:profile",
                runner_image_digest="sha256:runner",
                started_at="2026-07-24T08:00:00Z",
                finished_at="2026-07-24T08:01:00Z",
                checks=checks_path,
            )
            bundle = evidence_bundle.build_bundle(args)
        finally:
            os.unlink(checks_path)

        self.assertEqual("agentgate.io/evidence/v2", bundle["schema_version"])
        self.assertEqual(
            ["flutter-analyze", "flutter-test"],
            [item["id"] for item in bundle["checks"]],
        )
        problems = evidence_bundle.verify_bundle(
            bundle,
            {
                "source_sha": "source",
                "target_sha": "target",
                "merge_sha": "merge",
                "policy_digest": "sha256:policy",
                "profile_digest": "sha256:profile",
            },
        )
        self.assertEqual([], problems)

    def test_verify_bundle_reports_binding_mismatch(self) -> None:
        bundle = {
            "schema_version": "agentgate.io/evidence/v2",
            "source_sha": "source",
            "target_sha": "target",
            "merge_sha": "merge",
            "policy_digest": "sha256:policy",
            "profile_digest": "sha256:profile",
            "checks": [{"id": "unit", "status": "pass"}],
        }
        problems = evidence_bundle.verify_bundle(bundle, {"source_sha": "other"})
        self.assertEqual(["source_sha_mismatch"], problems)


class RiskMergeDecisionTests(unittest.TestCase):
    def _bundle(self, status: str = "pass") -> dict:
        return {
            "schema_version": "agentgate.io/evidence/v2",
            "source_sha": "source",
            "target_sha": "target",
            "merge_sha": "merge",
            "policy_digest": "sha256:policy",
            "profile_digest": "sha256:profile",
            "checks": [
                {"id": "flutter-analyze", "status": status},
                {"id": "flutter-test", "status": "pass"},
            ],
        }

    def _profile(self) -> dict:
        return {
            "risk_paths": {
                "high": ["lib/**/auth/**"],
                "critical": ["governance/**", ".gitlab-ci.yml"],
            }
        }

    def test_medium_clean_change_auto_merges(self) -> None:
        decision = risk_merge_decision.build_decision(
            bundle=self._bundle(),
            profile=self._profile(),
            changed_paths=["lib/book/page.dart"],
            declared_risk="low",
        )

        self.assertEqual("PASS", decision["status"])
        self.assertEqual("AUTO_MERGE", decision["merge_action"])
        self.assertEqual("medium", decision["risk"])

    def test_high_risk_waits_without_independent_approval(self) -> None:
        decision = risk_merge_decision.build_decision(
            bundle=self._bundle(),
            profile=self._profile(),
            changed_paths=["lib/app/auth/login.dart"],
            declared_risk="low",
            approvals=[],
            author="alice",
        )

        self.assertEqual("WAITING_APPROVAL", decision["status"])
        self.assertEqual("WAIT", decision["merge_action"])
        self.assertEqual("high", decision["risk"])
        self.assertIn("approval_missing", decision["blocking_reasons"])

    def test_high_risk_auto_merges_after_valid_approval(self) -> None:
        decision = risk_merge_decision.build_decision(
            bundle=self._bundle(),
            profile=self._profile(),
            changed_paths=["lib/app/auth/login.dart"],
            declared_risk="medium",
            approvals=[{"approver": "bob", "source_sha": "source"}],
            author="alice",
        )

        self.assertEqual("PASS", decision["status"])
        self.assertEqual("AUTO_MERGE", decision["merge_action"])
        self.assertEqual(1, decision["approvals"]["valid"])

    def test_self_and_stale_approvals_do_not_count(self) -> None:
        approvals = [
            {"approver": "alice", "source_sha": "source"},
            {"approver": "bob", "source_sha": "old"},
            {"approver": "carol", "source_sha": "source"},
        ]

        valid = risk_merge_decision.valid_approvals(
            approvals,
            source_sha="source",
            author="alice",
        )

        self.assertEqual(["carol"], [item["approver"] for item in valid])

    def test_critical_risk_requires_manual_merge_after_two_approvals(self) -> None:
        decision = risk_merge_decision.build_decision(
            bundle=self._bundle(),
            profile=self._profile(),
            changed_paths=["governance/config.yml"],
            declared_risk="low",
            approvals=[
                {"approver": "bob", "source_sha": "source"},
                {"approver": "carol", "source_sha": "source"},
            ],
            author="alice",
        )

        self.assertEqual("PASS", decision["status"])
        self.assertEqual("MANUAL_MERGE", decision["merge_action"])
        self.assertEqual("critical", decision["risk"])

    def test_evidence_binding_mismatch_returns_error(self) -> None:
        decision = risk_merge_decision.build_decision(
            bundle=self._bundle(),
            profile=self._profile(),
            changed_paths=["lib/book/page.dart"],
            expected={"source_sha": "other"},
        )

        self.assertEqual("ERROR", decision["status"])
        self.assertEqual("BLOCK", decision["merge_action"])
        self.assertIn("source_sha_mismatch", decision["blocking_reasons"])

    def test_audit_log_appends_json_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit" / "merge-decisions.jsonl"
            decision = {"status": "PASS", "source_sha": "source"}
            risk_merge_decision.append_audit(str(path), decision)

            lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(1, len(lines))
        self.assertEqual("PASS", json.loads(lines[0])["status"])


class TestFileExemptionTests(unittest.TestCase):
    """P0-1: 测试文件对大多数内置规则豁免, 只保留 skipped-test。"""

    def setUp(self) -> None:
        self.cfg = json.loads(json.dumps(scan_risks.DEFAULT_CONFIG))
        self.cfg["risk_annotations"]["enforcement"] = "hard"

    def _make_diff(self, path: str, content: str) -> str:
        return f"+++ b/{path}\n@@ -0,0 +1 @@\n+{content}\n"

    def test_magic_id_in_go_test_file_is_exempt(self) -> None:
        """Go 测试文件里的长数字 ID (如身份证) 不触发 magic-id。"""
        diff = self._make_diff(
            "service/user/user_test.go",
            'idcard := "110101199001011234"',
        )
        violations = scan_risks.scan(diff, self.cfg)
        types = [v["type"] for v in violations]
        self.assertNotIn("magic-id", types)

    def test_auth_bypass_in_go_test_file_is_exempt(self) -> None:
        """Go 测试文件里的断言比较 (userId == "...") 不触发 auth-bypass。"""
        diff = self._make_diff(
            "pkg/auth/auth_test.go",
            'assert.Equal(t, userId, "test-user")',
        )
        violations = scan_risks.scan(diff, self.cfg)
        types = [v["type"] for v in violations]
        self.assertNotIn("auth-bypass", types)

    def test_skipped_test_in_python_test_file_is_still_detected(self) -> None:
        """Python 测试文件里 skip 标注仍然应当被检测 (skipped-test 在 _TEST_FILE_PATTERNS 中)。"""
        diff = self._make_diff(
            "tests/test_payment.py",
            "@pytest.mark.skip",
        )
        violations = scan_risks.scan(diff, self.cfg)
        types = [v["type"] for v in violations]
        self.assertIn("skipped-test", types)

    def test_magic_id_in_production_go_file_is_detected(self) -> None:
        """非测试 Go 文件的 magic-id 仍然触发。"""
        diff = self._make_diff(
            "service/user/user.go",
            'adminID := "110101199001011234"',
        )
        violations = scan_risks.scan(diff, self.cfg)
        types = [v["type"] for v in violations]
        self.assertIn("magic-id", types)

    def test_go_test_func_removal_is_detected(self) -> None:
        """P0-1: 删除 Go 的 func TestXxx 应被 test-removal 检测到。"""
        diff = (
            "-func TestPayment(t *testing.T) {\n"
            f'+// risk:test-removal reason:"consolidated into TestPaymentV2" '
            f'owner:@qa reviewed:{dt.date.today().isoformat()}\n'
        )
        problems = scan_risks.check_test_removal(diff, self.cfg)
        self.assertEqual([], problems)

    def test_go_test_func_removal_without_annotation_is_flagged(self) -> None:
        diff = "-func TestRefund(t *testing.T) {\n"
        problems = scan_risks.check_test_removal(diff, self.cfg)
        self.assertTrue(problems)


class ExtensionFilterTests(unittest.TestCase):
    """P0-2: 内置规则按扩展名过滤, Go 文件不被 C#/JS 专属规则触发。"""

    def setUp(self) -> None:
        self.cfg = json.loads(json.dumps(scan_risks.DEFAULT_CONFIG))
        self.cfg["risk_annotations"]["enforcement"] = "hard"

    def _make_diff(self, path: str, content: str) -> str:
        return f"+++ b/{path}\n@@ -0,0 +1 @@\n+{content}\n"

    def test_swallowed_exception_not_triggered_on_go_file(self) -> None:
        """swallowed-exception (catch 语法) 不应在 Go 文件触发。"""
        diff = self._make_diff("pkg/foo/foo.go", "catch (Exception) { }")
        violations = scan_risks.scan(diff, self.cfg)
        types = [v["type"] for v in violations]
        self.assertNotIn("swallowed-exception", types)

    def test_swallowed_exception_triggered_on_cs_file(self) -> None:
        """swallowed-exception 在 C# 文件仍然触发。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            cs_file = Path(tmp) / "Service.cs"
            cs_file.write_text("catch (Exception) { }\n", encoding="utf-8")
            diff = (
                f"+++ b/{cs_file.as_posix()}\n"
                "@@ -0,0 +1 @@\n"
                "+catch (Exception) { }\n"
            )
            violations = scan_risks.scan(diff, self.cfg)
        types = [v["type"] for v in violations]
        self.assertIn("swallowed-exception", types)

    def test_skipped_test_not_triggered_on_go_file(self) -> None:
        """skipped-test ([Fact(Skip=) / @pytest) 不适用于 Go 文件。"""
        diff = self._make_diff("pkg/foo/foo_test.go", '@pytest.mark.skip')
        violations = scan_risks.scan(diff, self.cfg)
        # 测试文件豁免会先过滤掉, 但即使是生产 .go 文件 skipped-test 也不应命中
        diff2 = self._make_diff("pkg/foo/foo.go", '@pytest.mark.skip')
        violations2 = scan_risks.scan(diff2, self.cfg)
        types = [v["type"] for v in violations2]
        self.assertNotIn("skipped-test", types)


class PatternIncludesTests(unittest.TestCase):
    """P1-3: pattern_includes 从外部 YAML 加载规则, 类型自动注册, mode:warn 不阻断。"""

    def setUp(self) -> None:
        self.cfg = json.loads(json.dumps(scan_risks.DEFAULT_CONFIG))
        self.cfg["risk_annotations"]["enforcement"] = "hard"

    def _scan_go(self, code_line: str, patterns_yml: str) -> list[dict]:
        """用临时 patterns 文件和临时 Go 源文件构造 diff 并扫描。"""
        import tempfile, yaml as _yaml  # noqa: E401
        with tempfile.TemporaryDirectory() as tmp:
            # 写 patterns yml
            pat_path = os.path.join(tmp, "go.yml")
            with open(pat_path, "w", encoding="utf-8") as f:
                f.write(patterns_yml)
            # 加载 config 并注入 includes
            cfg = json.loads(json.dumps(self.cfg))
            cfg["risk_annotations"]["pattern_includes"] = [pat_path]
            scan_risks._load_pattern_includes(cfg, None)
            # 写 Go 源文件
            src = Path(tmp) / "service.go"
            src.write_text(code_line + "\n", encoding="utf-8")
            diff = (
                f"+++ b/{src.as_posix()}\n"
                "@@ -0,0 +1 @@\n"
                f"+{code_line}\n"
            )
            return scan_risks.scan(diff, cfg)

    def test_go_swallowed_error_warn_does_not_block(self) -> None:
        """patterns/go.yml 的 swallowed-error (mode:warn) 命中但不阻断。"""
        yml = (
            "patterns:\n"
            "  - type: swallowed-error\n"
            '    regex: \'\\b_\\s*=\\s*\\w[\\w.]*\\(\'\n'
            "    desc: 显式丢弃 error\n"
            "    exts: [\".go\"]\n"
            "    mode: warn\n"
        )
        violations = self._scan_go("_ = db.Close()", yml)
        self.assertTrue(violations, "应有 warn 违规")
        self.assertEqual("warn", violations[0]["mode"])

    def test_warn_violation_does_not_cause_hard_exit(self) -> None:
        """mode:warn 违规在 hard enforcement 下不返回退出码 1。"""
        import tempfile
        yml = (
            "patterns:\n"
            "  - type: swallowed-error\n"
            '    regex: \'\\b_\\s*=\\s*\\w[\\w.]*\\(\'\n'
            "    desc: 显式丢弃 error\n"
            "    exts: [\".go\"]\n"
            "    mode: warn\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            pat_path = os.path.join(tmp, "go.yml")
            with open(pat_path, "w", encoding="utf-8") as f:
                f.write(yml)
            src = Path(tmp) / "service.go"
            src.write_text("_ = db.Close()\n", encoding="utf-8")
            diff_path = os.path.join(tmp, "test.diff")
            with open(diff_path, "w", encoding="utf-8") as f:
                f.write(
                    f"+++ b/{src.as_posix()}\n"
                    "@@ -0,0 +1 @@\n"
                    "+_ = db.Close()\n"
                )
            cfg_path = os.path.join(tmp, "governance.config.yml")
            with open(cfg_path, "w", encoding="utf-8") as f:
                f.write(
                    "risk_annotations:\n"
                    "  enforcement: hard\n"
                    f"  pattern_includes:\n"
                    f"    - {pat_path}\n"
                    "  registered_types:\n"
                    "    - swallowed-error\n"
                )
            with mock.patch.object(
                sys, "argv",
                ["scan_risks.py", "--diff-file", diff_path, "--config", cfg_path]
            ):
                rc = scan_risks.main()
        self.assertEqual(0, rc, "warn-only 违规不应阻断 (exit 0)")

    def test_mixed_warn_and_block_patterns_result_in_block(self) -> None:
        """同一行同时命中 warn 和 block 时, 最终模式应为 block。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "service.go"
            src.write_text("Danger()\n", encoding="utf-8")
            cfg = json.loads(json.dumps(self.cfg))
            cfg["risk_annotations"]["registered_types"].extend(
                ["danger-warn", "danger-block"]
            )
            cfg["risk_annotations"]["custom_patterns"] = [
                {
                    "type": "danger-warn",
                    "regex": r"Danger\(",
                    "desc": "warn-only dangerous call",
                    "exts": [".go"],
                    "mode": "warn",
                },
                {
                    "type": "danger-block",
                    "regex": r"Danger\(",
                    "desc": "blocking dangerous call",
                    "exts": [".go"],
                    "mode": "block",
                },
            ]
            diff = (
                f"+++ b/{src.as_posix()}\n"
                "@@ -0,0 +1 @@\n"
                "+Danger()\n"
            )
            violations = scan_risks.scan(diff, cfg)
        self.assertEqual("block", violations[0]["mode"])
        self.assertEqual("danger-block/danger-warn", violations[0]["type"])

    def test_go_pattern_not_triggered_on_cs_file(self) -> None:
        """Go 专属规则 (exts:[.go]) 不对 .cs 文件触发。"""
        yml = (
            "patterns:\n"
            "  - type: sensitive-log\n"
            '    regex: \'\\bpassword\\b\'\n'
            "    desc: 敏感字段进日志\n"
            "    exts: [\".go\"]\n"
            "    mode: warn\n"
        )
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            pat_path = os.path.join(tmp, "go.yml")
            with open(pat_path, "w", encoding="utf-8") as f:
                f.write(yml)
            cfg = json.loads(json.dumps(self.cfg))
            cfg["risk_annotations"]["pattern_includes"] = [pat_path]
            scan_risks._load_pattern_includes(cfg, None)
            src = Path(tmp) / "Service.cs"
            src.write_text('log.Info("password=" + pwd);\n', encoding="utf-8")
            diff = (
                f"+++ b/{src.as_posix()}\n"
                "@@ -0,0 +1 @@\n"
                '+log.Info("password=" + pwd);\n'
            )
            violations = scan_risks.scan(diff, cfg)
        types = [v["type"] for v in violations]
        self.assertNotIn("sensitive-log", types)

    def test_pattern_includes_missing_file_is_skipped(self) -> None:
        """不存在的 pattern_includes 路径不抛异常, 仅打印警告。"""
        cfg = json.loads(json.dumps(self.cfg))
        cfg["risk_annotations"]["pattern_includes"] = ["/nonexistent/rules.yml"]
        try:
            scan_risks._load_pattern_includes(cfg, None)
        except Exception as exc:
            self.fail(f"不应抛异常: {exc}")


class GoPatternHardeningTests(unittest.TestCase):
    """Regression coverage for the Go rule pack shipped in patterns/go.yml."""

    def setUp(self) -> None:
        self.cfg = json.loads(json.dumps(scan_risks.DEFAULT_CONFIG))
        self.cfg["risk_annotations"]["enforcement"] = "hard"
        self.cfg["risk_annotations"]["pattern_includes"] = [
            str(ROOT / "patterns" / "go.yml")
        ]
        scan_risks._load_pattern_includes(self.cfg, None)

    def _scan_go(self, source: str, filename: str = "service.go") -> list[dict]:
        code = source.strip("\n")
        lines = code.splitlines()
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / filename
            src.write_text(code + "\n", encoding="utf-8")
            diff_lines = [
                f"+++ b/{src.as_posix()}",
                f"@@ -0,0 +1,{len(lines)} @@",
                *[f"+{line}" for line in lines],
            ]
            return scan_risks.scan("\n".join(diff_lines) + "\n", self.cfg)

    @staticmethod
    def _types(violations: list[dict]) -> set[str]:
        out: set[str] = set()
        for item in violations:
            out.update(item["type"].split("/"))
        return out

    def assertHits(self, risk_type: str, source: str) -> None:
        violations = self._scan_go(source)
        self.assertIn(risk_type, self._types(violations), violations)
        matched = [v for v in violations if risk_type in v["type"].split("/")]
        self.assertTrue(all(v["mode"] == "warn" for v in matched), matched)

    def assertDoesNotHit(self, risk_type: str, source: str) -> None:
        violations = self._scan_go(source)
        self.assertNotIn(risk_type, self._types(violations), violations)

    def test_go_cmd_inject_multiline_hits(self) -> None:
        self.assertHits(
            "go-cmd-inject",
            '''
cmd := exec.Command(
    "sh",
    "-c",
    "echo " + userInput,
)
''',
        )

    def test_go_cmd_inject_constant_args_do_not_hit(self) -> None:
        self.assertDoesNotHit(
            "go-cmd-inject",
            'cmd := exec.Command("tool", "--version")',
        )

    def test_go_ssrf_new_request_multiline_hits(self) -> None:
        self.assertHits(
            "go-ssrf",
            '''
req, _ := http.NewRequest(
    "GET",
    "https://api.example.test/" + userPath,
    nil,
)
client.Do(req)
''',
        )

    def test_go_ssrf_constant_url_does_not_hit(self) -> None:
        self.assertDoesNotHit(
            "go-ssrf",
            'resp, _ := http.Get("https://api.example.test/health")',
        )

    def test_go_sql_concat_common_forms_hit(self) -> None:
        samples = [
            'query := fmt.Sprintf("SELECT * FROM users WHERE id = %s", userID)',
            'query := "SELECT * FROM users WHERE id = " + userID',
            '''
query := "SELECT * FROM users WHERE name = '" +
    userName + "'"
''',
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertHits("go-sql-concat", sample)

    def test_go_sql_parameterized_query_does_not_hit(self) -> None:
        self.assertDoesNotHit(
            "go-sql-concat",
            'rows, _ := db.Query("SELECT * FROM users WHERE id = ?", userID)',
        )

    def test_go_hardcoded_secret_literals_hit(self) -> None:
        samples = [
            'password := "placeholder-secret-value"',
            "cfg := Config{ClientSecret: `placeholder-secret-value`}",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertHits("go-hardcoded-secret", sample)

    def test_go_hardcoded_secret_non_literals_do_not_hit(self) -> None:
        samples = [
            'token := os.Getenv("TOKEN")',
            'tokenCount := 3',
            'token := "short"',
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertDoesNotHit("go-hardcoded-secret", sample)

    def test_go_tls_skip_verify_hits(self) -> None:
        self.assertHits(
            "go-tls-skip-verify",
            "cfg := &tls.Config{InsecureSkipVerify: true}",
        )

    def test_go_tls_normal_config_does_not_hit(self) -> None:
        self.assertDoesNotHit(
            "go-tls-skip-verify",
            "cfg := &tls.Config{MinVersion: tls.VersionTLS12}",
        )

    def test_go_panic_hits_business_file(self) -> None:
        self.assertHits("go-panic-in-handler", 'panic("unexpected state")')

    def test_go_panic_absent_does_not_hit(self) -> None:
        self.assertDoesNotHit("go-panic-in-handler", "return fmt.Errorf(\"bad state\")")

    def test_go_weak_random_security_context_hits(self) -> None:
        self.assertHits("go-weak-random", "token := rand.Intn(1000000)")

    def test_go_weak_random_sampling_context_does_not_hit(self) -> None:
        self.assertDoesNotHit("go-weak-random", "sample := rand.Intn(10)")


class RunAffectedTestsTests(unittest.TestCase):
    """P1-4: run_affected_tests.py 的核心逻辑单元测试。"""

    def setUp(self) -> None:
        run_affected = importlib.import_module("run_affected_tests")
        self.run_affected = run_affected

    def test_affected_packages_extracts_go_dirs(self) -> None:
        diff_output = (
            "pkg/payment/pay.go\n"
            "pkg/payment/pay_test.go\n"
            "pkg/user/user.go\n"
            "README.md\n"
        )
        pkgs = self.run_affected.affected_packages(diff_output)
        self.assertIn("pkg/payment", pkgs)
        self.assertIn("pkg/user", pkgs)
        self.assertNotIn("README.md", pkgs)

    def test_affected_packages_empty_diff(self) -> None:
        self.assertEqual([], self.run_affected.affected_packages(""))

    def test_affected_packages_no_go_files(self) -> None:
        diff_output = "docs/README.md\nci/pipeline.yml\n"
        self.assertEqual([], self.run_affected.affected_packages(diff_output))

    def test_run_tests_empty_packages_returns_zero(self) -> None:
        rc = self.run_affected.run_tests([], timeout=30)
        self.assertEqual(0, rc)

    def test_find_go_module_root_returns_none_outside_module(self) -> None:
        with mock.patch("os.path.isfile", return_value=False):
            result = self.run_affected.find_go_module_root()
        self.assertIsNone(result)


class AiUsageNotMandatoryTests(unittest.TestCase):
    """AI-Usage 不再是强制字段: 无 trailer 无描述时不阻断。"""

    def _mr_desc(self, extra: str = "") -> str:
        return (
            "## 背景\n\n修复支付超时问题。\n\n"
            "## 变更内容\n\n- 增加重试逻辑\n\n"
            "## 自测确认\n\n- [x] 本地测试通过\n"
            + extra
        )

    def test_no_ai_usage_passes_by_default(self) -> None:
        """DEFAULT_CONFIG 不含 ai_usage, 缺少 trailer/描述不应产生 problem。"""
        cfg = validate_mr.load_config(None)
        problems = validate_mr.validate(self._mr_desc(), cfg, None)
        ai_problems = [p for p in problems if "AI-Usage" in p]
        self.assertEqual([], ai_problems, f"不应有 AI-Usage 问题: {ai_problems}")

    def test_ai_usage_optional_when_not_in_mandatory_fields(self) -> None:
        """mandatory_fields 中不含 ai_usage 时, 校验器不检查该字段。"""
        cfg = {"metadata": {"enforcement": "hard", "mandatory_fields": ["background", "changes", "self_test"]},
               "large_change": validate_mr.DEFAULT_CONFIG["large_change"]}
        problems = validate_mr.validate(self._mr_desc(), cfg, None)
        ai_problems = [p for p in problems if "AI-Usage" in p]
        self.assertEqual([], ai_problems)

    def test_ai_usage_still_checked_when_in_mandatory_fields(self) -> None:
        """显式在 mandatory_fields 里加回 ai_usage 时仍然校验。"""
        cfg = {"metadata": {
                   "enforcement": "hard",
                   "mandatory_fields": ["background", "changes", "self_test", "ai_usage"],
               },
               "large_change": validate_mr.DEFAULT_CONFIG["large_change"]}
        with mock.patch.object(validate_mr, "find_ai_usage_in_commits", return_value=(False, None)):
            problems = validate_mr.validate(self._mr_desc(), cfg, None)
        ai_problems = [p for p in problems if "AI-Usage" in p or "ai_usage" in p.lower()]
        self.assertTrue(ai_problems, "显式加回 ai_usage 后应当校验")


class TestedTrailerValidationTests(unittest.TestCase):
    """validate_mr 应能从 commit trailer 检查 Tested: 字段。"""

    def _base_cfg(self) -> dict:
        return {
            "metadata": {
                "enforcement": "hard",
                "mandatory_fields": ["background", "changes", "self_test", "tested"],
            },
            "large_change": validate_mr.DEFAULT_CONFIG["large_change"],
        }

    def _mr_desc(self) -> str:
        return (
            "## 背景\n\n修复支付超时问题。\n\n"
            "## 变更内容\n\n- 增加重试逻辑\n\n"
            "## 自测确认\n\n- [x] 本地测试通过\n"
        )

    def test_passes_when_tested_trailer_is_pass(self) -> None:
        """commit 里有 Tested: pass 时不应报错。"""
        cfg = self._base_cfg()
        with mock.patch.object(validate_mr, "find_tested_trailer_in_commits", return_value="pass"):
            problems = validate_mr.validate(self._mr_desc(), cfg, None)
        tested_problems = [p for p in problems if "Tested" in p]
        self.assertEqual([], tested_problems, f"不应有 Tested 问题: {tested_problems}")

    def test_fails_when_no_tested_trailer(self) -> None:
        """没有 Tested: trailer 时应报缺失错误。"""
        cfg = self._base_cfg()
        with mock.patch.object(validate_mr, "find_tested_trailer_in_commits", return_value=None):
            problems = validate_mr.validate(self._mr_desc(), cfg, None)
        self.assertTrue(
            any("Tested" in p and "trailer" in p for p in problems),
            f"应报 Tested trailer 缺失, 实际: {problems}",
        )

    def test_fails_when_tested_trailer_is_fail(self) -> None:
        """Tested: fail 应硬拒，不允许合并失败的测试。"""
        cfg = self._base_cfg()
        with mock.patch.object(validate_mr, "find_tested_trailer_in_commits", return_value="fail"):
            problems = validate_mr.validate(self._mr_desc(), cfg, None)
        self.assertTrue(
            any("fail" in p.lower() and "Tested" in p for p in problems),
            f"应报 Tested: fail 问题, 实际: {problems}",
        )

    def test_not_checked_when_not_in_mandatory_fields(self) -> None:
        """tested 不在 mandatory_fields 时不应检查。"""
        cfg = {
            "metadata": {
                "enforcement": "hard",
                "mandatory_fields": ["background", "changes", "self_test"],
            },
            "large_change": validate_mr.DEFAULT_CONFIG["large_change"],
        }
        with mock.patch.object(validate_mr, "find_tested_trailer_in_commits", return_value=None):
            problems = validate_mr.validate(self._mr_desc(), cfg, None)
        tested_problems = [p for p in problems if "Tested" in p]
        self.assertEqual([], tested_problems, "mandatory_fields 无 tested 时不应检查")


class WarnJobSummaryTests(unittest.TestCase):
    """warn 命中写入 GITHUB_STEP_SUMMARY, block 为零时不写失败表格。"""

    def setUp(self) -> None:
        self.cfg = json.loads(json.dumps(scan_risks.DEFAULT_CONFIG))
        self.cfg["risk_annotations"]["enforcement"] = "hard"

    def _run_scan_with_summary(self, diff: str, cfg: dict) -> tuple[list[dict], str]:
        """运行 scan 并捕获写入 GITHUB_STEP_SUMMARY 的内容。"""
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            summary_path = f.name
        try:
            with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary_path}):
                violations = scan_risks.scan(diff, cfg)
                blocking = [v for v in violations if v.get("mode") != "warn"]
                warn_only = [v for v in violations if v.get("mode") == "warn"]
                scan_risks._write_summary(blocking, warn_only)
            with open(summary_path, encoding="utf-8") as f:
                summary = f.read()
        finally:
            os.unlink(summary_path)
        return violations, summary

    def test_pass_writes_green_summary(self) -> None:
        diff = "+++ b/pkg/foo/foo.go\n@@ -0,0 +1 @@\n+func Hello() {}\n"
        _, summary = self._run_scan_with_summary(diff, self.cfg)
        self.assertIn("✅", summary)
        self.assertNotIn("❌", summary)
        self.assertNotIn("⚠️", summary)

    def test_warn_violation_appears_in_summary(self) -> None:
        """mode:warn 规则的命中出现在 Job Summary 中, 门禁不阻断。"""
        yml = (
            "patterns:\n"
            "  - type: sensitive-log\n"
            '    regex: \'\\bpassword\\b\'\n'
            "    desc: 敏感字段进日志\n"
            "    exts: [\".go\"]\n"
            "    mode: warn\n"
        )
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            pat_path = os.path.join(tmp, "go.yml")
            with open(pat_path, "w", encoding="utf-8") as f:
                f.write(yml)
            src = Path(tmp) / "service.go"
            src.write_text('log.Info("password=" + pwd)\n', encoding="utf-8")
            diff = (
                f"+++ b/{src.as_posix()}\n"
                "@@ -0,0 +1 @@\n"
                '+log.Info("password=" + pwd)\n'
            )
            cfg = json.loads(json.dumps(self.cfg))
            cfg["risk_annotations"]["pattern_includes"] = [pat_path]
            scan_risks._load_pattern_includes(cfg, None)
            _, summary = self._run_scan_with_summary(diff, cfg)

        self.assertIn("⚠️", summary)
        self.assertNotIn("❌", summary)
        self.assertIn("warn", summary.lower())

    def test_no_summary_file_when_env_not_set(self) -> None:
        """未设置 GITHUB_STEP_SUMMARY 时, _write_summary 静默跳过不报错。"""
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"}
        with mock.patch.dict(os.environ, env, clear=True):
            try:
                scan_risks._write_summary([], [])
            except Exception as exc:
                self.fail(f"不应抛异常: {exc}")


class ReverseDepsTests(unittest.TestCase):
    """#8: run_affected_tests 反向依赖扩展。"""

    def setUp(self) -> None:
        self.ra = importlib.import_module("run_affected_tests")

    def _fake_go_list_json(self, pkgs: list[dict]) -> str:
        """生成 go list -json ./... 的输出格式（多个拼接 JSON 对象）。"""
        return "\n".join(json.dumps(p) for p in pkgs)

    def test_expand_with_importers_finds_dependent_pkg(self) -> None:
        """改了 pkg/db, importer pkg/service 应被加入测试集。"""
        pkgs = [
            {"ImportPath": "example.com/app/pkg/db", "Imports": []},
            {"ImportPath": "example.com/app/pkg/service",
             "Imports": ["example.com/app/pkg/db"]},
            {"ImportPath": "example.com/app/pkg/user",
             "Imports": ["example.com/app/pkg/service"]},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            # 写 go.mod
            with open(os.path.join(tmp, "go.mod"), "w") as f:
                f.write("module example.com/app\ngo 1.21\n")
            # mock go list 输出
            fake_out = self._fake_go_list_json(pkgs)
            completed = mock.Mock(returncode=0, stdout=fake_out, stderr="")
            with mock.patch.object(
                self.ra.subprocess, "run", return_value=completed
            ), mock.patch("os.path.abspath", side_effect=lambda p: os.path.join(tmp, p) if not os.path.isabs(p) else p):
                reverse_map = self.ra.build_reverse_dep_map(tmp)
                # pkg/db を直接改動した場合の拡張
                with mock.patch.object(self.ra, "find_go_module_root", return_value=tmp):
                    expanded = self.ra.expand_with_importers(
                        ["pkg/db"], tmp, reverse_map
                    )
        self.assertIn("pkg/db", expanded)
        self.assertIn("pkg/service", expanded)
        # pkg/user は直接依存していないので含まれない (1-hop のみ)
        self.assertNotIn("pkg/user", expanded)

    def test_no_reverse_deps_returns_direct_only(self) -> None:
        """--no-reverse-deps フラグ相当: 反向依赖图が空の場合は直接パッケージのみ。"""
        direct = ["pkg/payment", "pkg/order"]
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "go.mod"), "w") as f:
                f.write("module example.com/app\ngo 1.21\n")
            result = self.ra.expand_with_importers(direct, tmp, {})
        self.assertEqual(sorted(direct), sorted(result))

    def test_go_list_failure_falls_back_gracefully(self) -> None:
        """go list が失敗してもエラーにならず空の map を返す。"""
        failed = mock.Mock(returncode=1, stdout="", stderr="error")
        with mock.patch.object(
            self.ra.subprocess, "run", return_value=failed
        ):
            result = self.ra.build_reverse_dep_map("/some/dir")
        self.assertEqual({}, result)

    def test_get_module_name_reads_go_mod(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "go.mod"), "w") as f:
                f.write("module github.com/company/myapp\ngo 1.22\n")
            name = self.ra.get_module_name(tmp)
        self.assertEqual("github.com/company/myapp", name)

    def test_affected_packages_deduplicates_dirs(self) -> None:
        diff = "pkg/payment/pay.go\npkg/payment/refund.go\npkg/order/order.go\n"
        pkgs = self.ra.affected_packages(diff)
        self.assertEqual(["pkg/order", "pkg/payment"], pkgs)


class ScanIgnoreTests(unittest.TestCase):
    """#9: 行内 scan:ignore reason:"..." 豁免精确到行。"""

    def setUp(self) -> None:
        self.cfg = json.loads(json.dumps(scan_risks.DEFAULT_CONFIG))
        self.cfg["risk_annotations"]["enforcement"] = "hard"

    def _make_diff(self, path: str, lines: list[str]) -> str:
        hdr = f"+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n"
        return hdr + "".join(f"+{l}\n" for l in lines)

    def test_inline_ignore_on_same_line_suppresses_violation(self) -> None:
        """magic-id 后跟 scan:ignore reason 在同一行时豁免。"""
        diff = self._make_diff("service/pay.go", [
            '"110101199001011234"  // scan:ignore reason:"fixture for integration test"',
        ])
        violations = scan_risks.scan(diff, self.cfg)
        self.assertEqual([], violations)

    def test_inline_ignore_on_prev_line_suppresses_violation(self) -> None:
        """scan:ignore 在命中行上一行时同样豁免。"""
        diff = self._make_diff("service/pay.go", [
            "// scan:ignore reason:\"known test fixture for idcard format check\"",
            '"110101199001011234"',
        ])
        violations = scan_risks.scan(diff, self.cfg)
        self.assertEqual([], violations)

    def test_inline_ignore_without_reason_does_not_suppress(self) -> None:
        """scan:ignore 没有 reason 或 reason 太短时不豁免。"""
        diff = self._make_diff("service/pay.go", [
            '"110101199001011234"  // scan:ignore',
        ])
        violations = scan_risks.scan(diff, self.cfg)
        # 没有合法 reason 就不豁免, 仍然报违规
        types = [v["type"] for v in violations]
        self.assertIn("magic-id", types)

    def test_inline_ignore_two_lines_away_does_not_suppress(self) -> None:
        """scan:ignore 在命中行两行之外不豁免 (只看同行和上一行)。"""
        diff = self._make_diff("service/pay.go", [
            "// scan:ignore reason:\"known test fixture for idcard format check\"",
            "// some other comment",
            '"110101199001011234"',
        ])
        violations = scan_risks.scan(diff, self.cfg)
        types = [v["type"] for v in violations]
        self.assertIn("magic-id", types)

    def test_scan_ignore_in_non_adjacent_file_has_no_effect(self) -> None:
        """不同文件的 scan:ignore 不会跨文件豁免。"""
        diff = (
            "+++ b/other/file.go\n@@ -0,0 +1 @@\n"
            '// scan:ignore reason:"this is a completely different file"\n'
            "+++ b/service/pay.go\n@@ -0,0 +1 @@\n"
            '+adminId == "admin"\n'
        )
        violations = scan_risks.scan(diff, self.cfg)
        # pay.go 里的 auth-bypass 没有被 other/file.go 的 ignore 豁免
        files = [v["file"] for v in violations]
        self.assertTrue(any("pay.go" in f for f in files))


class LargeDiffSummaryTests(unittest.TestCase):
    """Tier 3: 大 diff 时向 GITHUB_STEP_SUMMARY 写拆分建议。"""

    def _numstat_output(self) -> str:
        return (
            "300\t50\tsrc/payment/pay.go\n"
            "100\t20\tsrc/order/order.go\n"
            "80\t10\tsrc/user/user.go\n"
        )

    def test_large_diff_writes_warning_to_summary(self) -> None:
        """净改动超阈值时, Job Summary 包含大变更警告和目录分布。"""
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            summary_path = f.name
        try:
            completed = mock.Mock(returncode=0, stdout=self._numstat_output())
            with mock.patch.object(validate_mr.subprocess, "run", return_value=completed), \
                 mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary_path}):
                validate_mr._write_large_diff_summary(
                    total=560, threshold=500,
                    reasons=["净改动 560 行 ≥ 500"],
                    diff_base="origin/main",
                    excluded=[],
                )
            with open(summary_path, encoding="utf-8") as f:
                content = f.read()
        finally:
            os.unlink(summary_path)

        self.assertIn("⚠️", content)
        self.assertIn("560", content)
        self.assertIn("src", content)

    def test_no_summary_when_env_not_set(self) -> None:
        """未设置 GITHUB_STEP_SUMMARY 时静默跳过。"""
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"}
        with mock.patch.dict(os.environ, env, clear=True):
            try:
                validate_mr._write_large_diff_summary(600, 500, [], None, [])
            except Exception as exc:
                self.fail(f"不应抛异常: {exc}")

    def test_small_diff_does_not_trigger(self) -> None:
        """未超阈值时不写 Summary (函数不被调用, 因为 is_large 为 False)。"""
        cfg = json.loads(json.dumps(validate_mr.DEFAULT_CONFIG))
        with mock.patch.object(
            validate_mr.subprocess, "run",
            return_value=mock.Mock(returncode=0, stdout="10\t5\tsrc/foo.go\n"),
        ):
            is_large, _ = validate_mr.detect_large_change(cfg, "origin/main")
        self.assertFalse(is_large)


class MRDescriptionEncodingTests(unittest.TestCase):
    def test_stdin_description_strips_utf8_bom_before_heading_match(self) -> None:
        text = "\ufeff## 背景\n测试背景\n\n## 变更内容\n测试变更\n\n## 自测确认\n已测试\n"
        with mock.patch.object(sys, "stdin", mock.Mock(isatty=lambda: False, read=lambda: text)):
            self.assertTrue(validate_mr._has_section(validate_mr.read_description(None), "背景"))

    def test_file_description_accepts_utf8_bom(self) -> None:
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8-sig") as f:
            f.write("## 背景\n测试背景\n")
            path = f.name
        try:
            self.assertTrue(validate_mr._has_section(validate_mr.read_description(path), "背景"))
        finally:
            os.unlink(path)


class ChineseContentValidationTests(unittest.TestCase):
    def test_chinese_content_validation_passes_with_sufficient_chinese(self) -> None:
        """MR描述包含足够中文字符时通过验证"""
        text = "## 背景\n这是一个测试背景，包含足够的中文内容来通过验证。\n\n## 变更内容\n修改了核心逻辑\n"
        self.assertTrue(validate_mr._check_chinese_content(text))

    def test_chinese_content_validation_fails_with_insufficient_chinese(self) -> None:
        """MR描述中文字符不足时验证失败"""
        text = "## Background\nThis is an English description without enough Chinese characters.\n"
        self.assertFalse(validate_mr._check_chinese_content(text))

    def test_chinese_content_validation_fails_with_mixed_but_insufficient(self) -> None:
        """MR描述混合语言但中文不足时验证失败"""
        text = "## 背景\nSome English text with only a few 中文字符 here.\n"
        self.assertFalse(validate_mr._check_chinese_content(text))

    def test_validate_includes_chinese_requirement(self) -> None:
        """验证函数应包含中文内容检查"""
        cfg = {
            "metadata": {"mandatory_fields": ["background", "changes"]},
            "large_change": {"enforcement": "soft"},
        }
        # 英文描述应该失败
        english_text = "## Background\nThis is English only.\n\n## Changes\nSome changes here.\n"
        problems = validate_mr.validate(english_text, cfg, None)
        self.assertTrue(any("中文" in p for p in problems), f"Expected Chinese requirement error, got: {problems}")

        # 中文描述应该通过
        chinese_text = "## 背景\n这是一个包含足够中文内容的测试描述，用于验证中文要求是否正常工作。\n\n## 变更内容\n修改了核心功能模块\n"
        problems = validate_mr.validate(chinese_text, cfg, None)
        self.assertFalse(any("中文" in p for p in problems), f"Chinese text should pass, but got: {problems}")


class GateDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = json.loads(json.dumps(gate_decision.DEFAULT_CONFIG))

    def test_clean_checks_are_auto_mergeable_by_default(self) -> None:
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["src/orders/service.py"],
            checks={
                "risk-scan": "pass",
                "secret-scan": "pass",
                "mr-validate": "pass",
                "test-check": "pass",
            },
            config=self.config,
        )
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["merge_action"], "AUTO_MERGE")
        self.assertEqual(result["risk_level"], "medium")

    def test_low_risk_uses_fast_ci_evidence_only(self) -> None:
        cfg = json.loads(json.dumps(gate_decision.DEFAULT_CONFIG))
        cfg["auto_merge"]["required_checks_by_risk"] = {
            "low": ["risk-scan", "secret-scan", "mr-validate"],
            "medium": ["risk-scan", "secret-scan", "mr-validate", "test-check"],
        }
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["docs/usage.md"],
            checks={
                "risk-scan": "pass",
                "secret-scan": "pass",
                "mr-validate": "pass",
            },
            config=cfg,
        )
        self.assertEqual(result["risk_level"], "low")
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["merge_action"], "AUTO_MERGE")
        self.assertEqual(
            ["risk-scan", "secret-scan", "mr-validate"],
            [item["name"] for item in result["required_checks"]],
        )

    def test_medium_risk_cannot_be_approved_by_tested_trailer_alone(self) -> None:
        cfg = json.loads(json.dumps(gate_decision.DEFAULT_CONFIG))
        cfg["auto_merge"]["required_checks_by_risk"] = {
            "medium": ["risk-scan", "secret-scan", "mr-validate", "test-check", "unit-test"],
        }
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["src/orders/service.py"],
            checks={
                "risk-scan": "pass",
                "secret-scan": "pass",
                "mr-validate": "pass",
                "test-check": "pass",
                # 没有 unit-test 这份 CI 证据, commit message 里的 Tested: pass 不会参与 GateResult。
            },
            config=cfg,
        )
        self.assertEqual(result["risk_level"], "medium")
        self.assertEqual(result["result"], "FAIL")
        self.assertIn("required_check_missing", result["blocking_reasons"])

    def test_protected_path_requires_human_approval(self) -> None:
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["scripts/scan_risks.py"],
            checks={
                "risk-scan": "pass",
                "secret-scan": "pass",
                "mr-validate": "pass",
                "test-check": "pass",
                "selftest": "pass",
            },
            config=self.config,
        )
        self.assertEqual(result["risk_level"], "critical")
        self.assertEqual(result["result"], "WAITING_APPROVAL")
        self.assertEqual(result["merge_action"], "WAIT")
        self.assertIn("protected_paths_changed", result["blocking_reasons"])

    def test_high_risk_uses_stronger_ci_plan_but_can_still_auto_merge(self) -> None:
        cfg = json.loads(json.dumps(gate_decision.DEFAULT_CONFIG))
        cfg["auto_merge"]["risk_paths"]["high"] = ["scripts/**"]
        cfg["auto_merge"]["required_checks_by_risk"] = {
            "high": ["risk-scan", "secret-scan", "mr-validate", "test-check", "python-test", "selftest"],
        }
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["scripts/report_expired.py"],
            checks={
                "risk-scan": "pass",
                "secret-scan": "pass",
                "mr-validate": "pass",
                "test-check": "pass",
                "python-test": "pass",
                "selftest": "pass",
            },
            config=cfg,
        )
        self.assertEqual(result["risk_level"], "high")
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["merge_action"], "AUTO_MERGE")

    def test_protected_branch_blocks_direct_push(self) -> None:
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["src/service.py"],
            checks={
                "risk-scan": "pass",
                "secret-scan": "pass",
                "mr-validate": "pass",
                "test-check": "pass",
            },
            config=self.config,
            target_branch="master",
        )
        self.assertEqual(result["result"], "FAIL")
        self.assertEqual(result["merge_action"], "BLOCK")
        self.assertIn("protected_branch_requires_mr", result["blocking_reasons"])

    def test_non_protected_branch_allows_auto_merge(self) -> None:
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["src/service.py"],
            checks={
                "risk-scan": "pass",
                "secret-scan": "pass",
                "mr-validate": "pass",
                "test-check": "pass",
            },
            config=self.config,
            target_branch="feature/my-feature",
        )
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["merge_action"], "AUTO_MERGE")

    # risk:test-removal reason:"原 skip 放行测试已改名并反转断言以覆盖严格阻断语义，测试覆盖未删除" owner:@wangwf reviewed:2026-07-31
    def test_required_language_check_skip_is_blocked(self) -> None:
        """必需语言检查返回 skip 时必须阻断合并。"""
        cfg = json.loads(json.dumps(gate_decision.DEFAULT_CONFIG))
        cfg["auto_merge"]["required_checks"] = ["risk-scan", "go-test"]
        cfg["auto_merge"]["required_checks_by_risk"] = {}
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["src/service.py"],
            checks={"risk-scan": "pass", "go-test": "skip"},
            config=cfg,
        )
        self.assertEqual(result["result"], "FAIL")
        self.assertEqual(result["merge_action"], "BLOCK")
        self.assertIn("required_check_failed", result["blocking_reasons"])

    # risk:test-removal reason:"原 missing 放行测试已改名并反转断言以覆盖严格阻断语义，测试覆盖未删除" owner:@wangwf reviewed:2026-07-31
    def test_required_language_check_missing_is_blocked(self) -> None:
        """必需语言检查结果缺失时必须阻断合并。"""
        cfg = json.loads(json.dumps(gate_decision.DEFAULT_CONFIG))
        cfg["auto_merge"]["required_checks"] = ["risk-scan", "go-test"]
        cfg["auto_merge"]["required_checks_by_risk"] = {}
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["src/service.py"],
            checks={"risk-scan": "pass"},  # go-test 完全缺失
            config=cfg,
        )
        self.assertEqual(result["result"], "FAIL")
        self.assertEqual(result["merge_action"], "BLOCK")
        self.assertIn("required_check_missing", result["blocking_reasons"])

    def test_language_check_fail_is_still_blocked(self) -> None:
        """go-test 返回 fail 时（测试真的挂了）必须阻断合并"""
        cfg = json.loads(json.dumps(gate_decision.DEFAULT_CONFIG))
        cfg["auto_merge"]["required_checks"] = ["risk-scan", "go-test"]
        cfg["auto_merge"]["required_checks_by_risk"] = {}
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["src/service.py"],
            checks={"risk-scan": "pass", "go-test": "fail"},
            config=cfg,
        )
        self.assertNotEqual(result["merge_action"], "AUTO_MERGE")
        self.assertIn("required_check_failed", result["blocking_reasons"])

    def test_non_language_check_missing_is_blocked(self) -> None:
        """非 language_checks 的 job 结果文件缺失时，仍然阻断合并"""
        cfg = json.loads(json.dumps(gate_decision.DEFAULT_CONFIG))
        cfg["auto_merge"]["required_checks"] = ["risk-scan", "mr-validate"]
        cfg["auto_merge"]["required_checks_by_risk"] = {}
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["src/service.py"],
            checks={"risk-scan": "pass"},  # mr-validate 缺失
            config=cfg,
        )
        self.assertNotEqual(result["merge_action"], "AUTO_MERGE")
        self.assertIn("required_check_missing", result["blocking_reasons"])

    # risk:test-removal reason:"原 Flutter 缺失放行测试已改名并反转断言以覆盖多语言必需检查阻断语义" owner:@wangwf reviewed:2026-07-31
    def test_multiple_required_language_checks_missing_are_blocked(self) -> None:
        """配置为必需的语言检查不能因 job 未触发而放行。"""
        cfg = json.loads(json.dumps(gate_decision.DEFAULT_CONFIG))
        cfg["auto_merge"]["required_checks"] = ["risk-scan", "go-test", "flutter-test"]
        cfg["auto_merge"]["required_checks_by_risk"] = {}
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["src/service.py"],
            checks={"risk-scan": "pass"},  # Go 和 Flutter 的 job 都未触发
            config=cfg,
        )
        self.assertEqual(result["result"], "FAIL")
        self.assertEqual(result["merge_action"], "BLOCK")
        self.assertIn("required_check_missing", result["blocking_reasons"])

    def test_protected_branch_pattern_wildcard(self) -> None:
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["src/service.py"],
            checks={"lint": "pass", "unit": "pass"},
            config=self.config,
            target_branch="release/v1.0.0",
        )
        self.assertEqual(result["result"], "FAIL")
        self.assertEqual(result["merge_action"], "BLOCK")
        self.assertIn("protected_branch_requires_mr", result["blocking_reasons"])

    def test_failed_check_blocks_and_is_not_retried_as_green(self) -> None:
        cfg = json.loads(json.dumps(gate_decision.DEFAULT_CONFIG))
        cfg["auto_merge"]["required_checks_by_risk"] = {}
        cfg["auto_merge"]["required_checks"] = ["lint", "unit"]
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["src/orders/service.py"],
            checks={"lint": "fail", "unit": "pass"},
            config=cfg,
        )
        self.assertEqual(result["result"], "FAIL")
        self.assertEqual(result["merge_action"], "BLOCK")
        self.assertIn("required_check_failed", result["blocking_reasons"])

    def test_missing_required_check_is_blocking(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["auto_merge"]["required_checks"] = ["lint", "unit"]
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["src/orders/service.py"],
            checks={"lint": "pass"},
            config=config,
        )
        self.assertEqual(result["result"], "FAIL")
        self.assertIn("required_check_missing", result["blocking_reasons"])

    def test_non_pass_check_status_is_blocking(self) -> None:
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["src/orders/service.py"],
            checks={"lint": "queued", "unit": "pass"},
            config=self.config,
        )
        self.assertEqual(result["result"], "FAIL")
        self.assertEqual(result["merge_action"], "BLOCK")

    def test_disabled_auto_merge_waits_even_when_checks_pass(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["auto_merge"]["enabled"] = False
        result = gate_decision.build_gate_result(
            source_sha="head", target_sha="base", policy_sha="policy",
            changed_paths=["src/orders/service.py"],
            checks={"lint": "pass", "unit": "pass"},
            config=config,
        )
        self.assertEqual(result["result"], "WAITING_APPROVAL")
        self.assertEqual(result["merge_action"], "WAIT")
        self.assertIn("auto_merge_disabled", result["blocking_reasons"])

    def test_cli_accepts_utf8_bom_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence.json"
            output = Path(directory) / "gate.json"
            evidence.write_text(
                '{"checks":{"risk-scan":"pass","secret-scan":"pass",'
                '"mr-validate":"pass","test-check":"pass","go-test":"pass",'
                '"selftest":"pass"}}',
                encoding="utf-8-sig",
            )
            with mock.patch.object(gate_decision, "_changed_paths", return_value=[]):
                with mock.patch.object(sys, "argv", [
                    "gate_decision.py", "--evidence", str(evidence),
                    "--source-sha", "head", "--target-sha", "base",
                    "--policy-sha", "policy", "--diff-base", "base",
                    "--output", str(output),
                ]):
                    self.assertEqual(gate_decision.main(), 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["result"], "PASS")

    def test_cli_rejects_evidence_bound_to_other_source_sha(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence.json"
            output = Path(directory) / "gate.json"
            evidence.write_text(
                json.dumps({
                    "schema_version": "agentgate.io/ci-evidence/v1",
                    "source_sha": "other-head",
                    "target_sha": "base",
                    "policy_sha": "policy",
                    "checks": {"risk-scan": "pass"},
                }),
                encoding="utf-8",
            )
            with mock.patch.object(gate_decision, "_changed_paths", return_value=[]):
                with mock.patch.object(sys, "argv", [
                    "gate_decision.py", "--evidence", str(evidence),
                    "--source-sha", "head", "--target-sha", "base",
                    "--policy-sha", "policy", "--diff-base", "base",
                    "--output", str(output),
                ]):
                    self.assertEqual(gate_decision.main(), 2)


class GitLabAutoMergeTemplateTests(unittest.TestCase):
    def test_central_gitlab_template_has_gate_and_merge_bot_guards(self) -> None:
        template = (ROOT / "ci" / "governance-ci.yml").read_text(encoding="utf-8")
        self.assertIn("governance:gate-decision:", template)
        self.assertIn("governance:auto-merge:", template)
        self.assertIn("GOVERNANCE_MERGE_BOT_TOKEN", template)
        self.assertIn("CI_MERGE_REQUEST_SOURCE_PROJECT_ID", template)
        # merge bot uses Python urllib, not curl --data-urlencode
        self.assertIn('"merge_when_pipeline_succeeds": "true"', template)
        self.assertIn("CI_MERGE_REQUEST_SOURCE_BRANCH_SHA", template)
        self.assertIn('"source_sha": os.environ["SOURCE_SHA"]', template)
        self.assertIn('"target_sha": os.environ["TARGET_SHA"]', template)
        self.assertIn('"policy_sha": os.environ["POLICY_SHA"]', template)
        for check in (
            "risk-scan", "secret-scan", "mr-validate", "test-check",
            "go-test", "flutter-test", "python-test", "node-test",
            "java-test", "dotnet-test", "rust-test",
        ):
            self.assertIn(f'read_check_result("{check}")', template)
        self.assertNotIn('if [ "$DECISION_EXIT" -gt 1 ]', template)
        self.assertNotIn("DECISION_EXIT=$?", template)

    def test_installed_policy_defaults_are_hard(self) -> None:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("metadata:\n  enforcement: hard", installer)
        self.assertIn("risk_annotations:\n  enforcement: hard", installer)
        self.assertIn("testing:\n  enforcement: hard", installer)
        self.assertIn("required_checks:\n    - risk-scan\n    - secret-scan\n    - mr-validate\n    - test-check", installer)
        self.assertNotIn("required_checks:\n    - risk-scan\n    - secret-scan\n    - mr-validate\n    - test-check\n    - go-test", installer)
        self.assertIn('- "governance/scripts/**"', installer)
        self.assertEqual("hard", scan_risks.DEFAULT_CONFIG["risk_annotations"]["enforcement"])
        self.assertEqual("hard", validate_mr.DEFAULT_CONFIG["metadata"]["enforcement"])
        self.assertEqual("hard", check_tested.DEFAULT_CONFIG["testing"]["enforcement"])

    def test_gitlab_template_uses_prebuilt_images_and_legacy_syntax(self) -> None:
        template = (ROOT / "ci" / "governance-ci.yml").read_text(encoding="utf-8")
        self.assertIn("GOVERNANCE_PY_IMAGE", template)
        self.assertIn("GOVERNANCE_SECRET_IMAGE", template)
        self.assertIn("governance:flutter-test:", template)
        self.assertIn("git --version", template)
        self.assertIn("python -c \"import yaml; print('pyyaml ok')\"", template)
        self.assertIn("dependencies:", template)
        self.assertNotIn("timeout:", template)
        self.assertNotIn("apt-get", template)
        self.assertNotIn("pip install -q pyyaml", template)
        self.assertNotIn("python:3.11-slim", template)
        self.assertNotIn("image: python:3.11", template)
        self.assertNotIn("rules:", template)
        self.assertNotIn("needs:", template)
        self.assertNotRegex(template, r"(?m)^\s+timeout:")
        self.assertNotIn("dotenv:", template)

    def test_gitlab_legacy_lessons_are_recorded(self) -> None:
        lessons = (ROOT / "lessons" / "gitlab-legacy-ci.yml").read_text(encoding="utf-8")
        self.assertIn("agentgate.io/lessons/v1", lessons)
        self.assertIn("gitlab_legacy.job_timeout_unsupported", lessons)
        self.assertIn("gitlab_legacy.modern_schema_unsupported", lessons)
        self.assertIn("gitlab_legacy.optional_language_image_pull", lessons)
        self.assertIn("enforcement: hard", lessons)

    def test_lessons_capture_legacy_ci_and_secret_history_rules(self) -> None:
        lessons = (ROOT / "lessons" / "gitlab-legacy-ci.yml").read_text(encoding="utf-8")
        self.assertIn("gitlab_legacy.job_timeout_unsupported", lessons)
        self.assertIn("gitlab_legacy.modern_schema_unsupported", lessons)
        self.assertIn("gitlab_legacy.governance_core_required_checks", lessons)
        self.assertIn("gitlab_legacy.secret_history_hard_block", lessons)
        self.assertIn("legacy GitLab templates must not contain a job-level timeout key", lessons)
        self.assertIn("installed policy defaults must not require language/runtime checks", lessons)
        self.assertIn("do not downgrade the finding to advisory", lessons)

    def test_installer_ships_gate_decision_and_gitlab_auto_merge_jobs(self) -> None:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('scripts/gate_decision.py"   | write_file "governance/scripts/gate_decision.py"', installer)
        self.assertIn("scripts/gitlab_controller.py", installer)
        self.assertIn("scripts/gitlab_mr_compat.py", installer)
        self.assertIn("scripts/agentgate.py", installer)
        self.assertIn("scripts/evidence_bundle.py", installer)
        self.assertIn("scripts/risk_merge_decision.py", installer)
        self.assertIn("profiles/flutter-mobile.yml", installer)
        self.assertIn('fetch_or_local "ci/governance-ci.yml" | write_file "governance/ci-snippet.yml"', installer)


if __name__ == "__main__":
    unittest.main()
