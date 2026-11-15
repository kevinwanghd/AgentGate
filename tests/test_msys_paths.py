from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

governance_common = importlib.import_module("governance_common")
governance_scan_all = importlib.import_module("governance_scan_all")


class MsysToHostTests(unittest.TestCase):
    """MSYS 路径转换只应在 MINGW/MSYS 环境发生，且失败时保持原路径不阻断流程。"""

    def test_passthrough_outside_msys(self) -> None:
        with mock.patch.object(governance_common, "_in_msys", return_value=False), \
                mock.patch.object(governance_common.subprocess, "run") as run:
            self.assertEqual(governance_common.msys_to_host("/c/foo"), "/c/foo")
            run.assert_not_called()

    def test_converts_inside_msys(self) -> None:
        proc = mock.Mock(returncode=0, stdout="C:\\foo\r\n")
        with mock.patch.object(governance_common, "_in_msys", return_value=True), \
                mock.patch.object(governance_common.subprocess, "run", return_value=proc):
            self.assertEqual(governance_common.msys_to_host("/c/foo"), "C:\\foo")

    def test_falls_back_on_cygpath_failure(self) -> None:
        with mock.patch.object(governance_common, "_in_msys", return_value=True), \
                mock.patch.object(governance_common.subprocess, "run", side_effect=OSError("no cygpath")):
            self.assertEqual(governance_common.msys_to_host("/c/foo"), "/c/foo")

    def test_host_python_uses_msys_to_host(self) -> None:
        with mock.patch.object(governance_common, "msys_to_host", return_value="C:\\py\\python.exe") as conv:
            self.assertEqual(governance_common.host_python(), "C:\\py\\python.exe")
            conv.assert_called_once_with(sys.executable)


class ScanAllPathRewriteTests(unittest.TestCase):
    """scan_all 调用子脚本时转换 MSYS 文件路径，但不动 git ref。"""

    def _run_capturing_cmd(self, cmd: list[str]) -> list[str]:
        proc = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(governance_scan_all, "msys_to_host", side_effect=lambda p: "HOST:" + p), \
                mock.patch.object(governance_scan_all.subprocess, "run", return_value=proc) as run:
            governance_scan_all.run("test", cmd)
        return run.call_args.args[0]

    def test_converts_absolute_msys_paths_only(self) -> None:
        fixed = self._run_capturing_cmd(
            ["python", "/c/repo/scripts/scan_risks.py", "--diff-base", "HEAD~1", "--file", "/tmp/pr.md"]
        )
        self.assertEqual(
            fixed,
            ["python", "HOST:/c/repo/scripts/scan_risks.py", "--diff-base", "HEAD~1", "--file", "HOST:/tmp/pr.md"],
        )

    def test_stdin_is_forwarded(self) -> None:
        import subprocess

        proc = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(governance_scan_all.subprocess, "run", return_value=proc) as run:
            governance_scan_all.run("test", ["python", "x.py"], stdin=subprocess.DEVNULL)
        self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()