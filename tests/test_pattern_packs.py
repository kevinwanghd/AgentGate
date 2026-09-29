"""patterns/*.yml 规则包回归: 每条修复过的规则都用正/反样例锁定意图。

正则按 scan_risks.build_custom_patterns 的方式编译 (re.compile(rx), 无额外 flags),
扫描窗口是最多 5 行用 "\\n" 拼接的文本, 所以多行样例也在这里覆盖。
"""
from __future__ import annotations

import importlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
scan_risks = importlib.import_module("scan_risks")


def load_rule(lang: str, rtype: str) -> dict:
    data = yaml.safe_load((ROOT / "patterns" / f"{lang}.yml").read_text(encoding="utf-8"))
    for item in data["patterns"]:
        if item["type"] == rtype:
            return item
    raise KeyError(f"{lang}:{rtype}")


class PackRuleCase(unittest.TestCase):
    def assertFires(self, lang: str, rtype: str, *samples: str) -> None:
        rx = re.compile(load_rule(lang, rtype)["regex"])
        for s in samples:
            self.assertTrue(rx.search(s), f"{lang}:{rtype} should fire on {s!r}")

    def assertSilent(self, lang: str, rtype: str, *samples: str) -> None:
        rx = re.compile(load_rule(lang, rtype)["regex"])
        for s in samples:
            self.assertFalse(rx.search(s), f"{lang}:{rtype} must not fire on {s!r}")


class EscapingTests(PackRuleCase):
    """单引号 YAML 里的 \\\\. 会变成字面反斜杠, 规则永远不命中。"""

    def test_java_read_object_fires(self) -> None:
        self.assertFires("java", "unsafe-deserialization",
                         "Object o = in.readObject();",
                         "new ObjectInputStream(sock.getInputStream())")

    def test_dart_raw_insert_update_fire(self) -> None:
        self.assertFires("dart", "sql-injection",
                         "db.rawInsert('INSERT INTO t VALUES(${v})')",
                         "db.rawUpdate('UPDATE t SET a=${v}')")


class SqlAlternationScopingTests(PackRuleCase):
    """裸 INSERT|UPDATE|DELETE 分支会拦截枚举/注释/常量名, 必须限定在 SQL 字面量内。"""

    def test_java_enum_and_constants_do_not_block(self) -> None:
        self.assertSilent("java", "sql-injection",
                          "return HttpMethod.DELETE;",
                          "// UPDATE docs later",
                          "private static final int UPDATE_USER = 1;",
                          'String.format("Hello %s", name)')

    def test_java_real_sql_still_blocks(self) -> None:
        self.assertFires("java", "sql-injection",
                         'String q = String.format("SELECT * FROM u WHERE id=%s", id);',
                         'em.createQuery("DELETE FROM U u WHERE u.id=" + id)')

    def test_python_non_sql_does_not_block(self) -> None:
        self.assertSilent("python", "sql-injection",
                          "# UPDATE docs",
                          "UPDATE_USER = 1",
                          "msg = '{} items'.format(n)",
                          "label = f'{name} was deleted'")

    def test_python_real_sql_still_blocks(self) -> None:
        self.assertFires("python", "sql-injection",
                         "cur.execute('SELECT * FROM u WHERE id=%s' % uid)",
                         "q = f\"SELECT * FROM u WHERE id={uid}\"",
                         "q = 'DELETE FROM u WHERE id={}'.format(uid)")

    def test_js_plain_concat_and_constants_do_not_block(self) -> None:
        self.assertSilent("javascript", "sql-injection",
                          "const c = a.concat(b)",
                          "case Action.DELETE:",
                          "// UPDATE docs")
        self.assertSilent("javascript", "command-injection",
                          "const c = a.concat(b)")

    def test_js_real_sql_and_command_still_block(self) -> None:
        self.assertFires("javascript", "sql-injection",
                         "db.query(`SELECT * FROM u WHERE id=${id}`)",
                         "const q = base + 'DELETE FROM u'",
                         "'SELECT * FROM u WHERE id='.concat(id)")
        self.assertFires("javascript", "command-injection",
                         "exec(`ls ${dir}`)",
                         "spawn('sh', ['-c'].concat(args))")


class PythonRuleTests(PackRuleCase):
    def test_re_compile_is_not_dangerous_eval(self) -> None:
        # re.compile 是正则编译, 与动态代码执行无关, 不应阻断
        self.assertSilent("python", "dangerous-eval",
                          "p = re.compile(r'x')", "rx = regex.compile(s)",
                          "recompile(x)")
        self.assertFires("python", "dangerous-eval",
                         "eval(expr)", "code = compile(src, 'f', 'exec')",
                         "__import__(name)")

    def test_swallowed_exception_variants(self) -> None:
        self.assertFires("python", "swallowed-exception",
                         "except: pass",
                         "except ValueError as e: pass",
                         "except (A, B):  pass  # ignore",
                         "except ValueError:\n    pass\nfoo()")
        self.assertSilent("python", "swallowed-exception",
                          "except ValueError:\n    log.exception('x')",
                          "except ValueError:\n    pass_through()")


class CsharpRuleTests(PackRuleCase):
    def test_fromsql_interpolated_is_safe(self) -> None:
        # EF Core 把 FromSql($"...") 的插值转换为参数, 不应阻断
        self.assertSilent("csharp", "ef-rawsql",
                          'ctx.Users.FromSql($"SELECT * FROM u WHERE id={id}")',
                          'ctx.Database.ExecuteSqlInterpolated($"DELETE FROM u WHERE id={id}")')
        self.assertFires("csharp", "ef-rawsql",
                         'ctx.Users.FromSqlRaw($"SELECT * FROM u WHERE id={id}")',
                         'ctx.Database.ExecuteSqlRaw("DELETE FROM u WHERE id=" + id)',
                         'await ctx.Database.ExecuteSqlRawAsync($@"DELETE FROM u WHERE id={id}")')


class GoRuleTests(PackRuleCase):
    def test_panic_rule_fires(self) -> None:
        # 模式保持 block: test_regressions.GoPatternHardeningTests 锁定了 block 语义, 降级需另行评审
        self.assertFires("go", "go-panic-in-handler", 'panic("unexpected state")')

    def test_tier_comment_names_real_rules(self) -> None:
        text = (ROOT / "patterns" / "go.yml").read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        modes = {p["type"]: p.get("mode", "block") for p in data["patterns"]}
        for line in text.splitlines():
            m = re.match(r"#\s*Tier \d \((block|warn)\):\s*(.+)", line)
            if not m:
                continue
            for name in (n.strip() for n in m.group(2).split("|")):
                self.assertIn(name, modes, f"tier comment names unknown rule {name}")
                self.assertEqual(modes[name], m.group(1), name)


class ExplicitExtsExtendScanTests(unittest.TestCase):
    """规则显式声明的 exts (.json/.mjs) 必须真的被扫描, 否则规则是死代码。"""

    def _scan(self, pack: str, filename: str, line: str) -> list[dict]:
        cfg = json.loads(json.dumps(scan_risks.DEFAULT_CONFIG))
        cfg["risk_annotations"]["enforcement"] = "hard"
        cfg["risk_annotations"]["pattern_includes"] = [str(ROOT / "patterns" / pack)]
        scan_risks._load_pattern_includes(cfg, None)
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / filename
            src.write_text(line + "\n", encoding="utf-8")
            diff = f"+++ b/{src.as_posix()}\n@@ -0,0 +1,1 @@\n+{line}\n"
            return scan_risks.scan(diff, cfg)

    @staticmethod
    def _types(violations: list[dict]) -> set[str]:
        return {t for v in violations for t in v["type"].split("/")}

    def test_appsettings_json_is_scanned(self) -> None:
        # 假凭据分段拼接: 源码不能出现 ≥12 字符的带引号值, 否则命中 secret-scan
        v = self._scan("csharp.yml", "appsettings.json",
                       '  "Password": "' + "Super" "Secret" "123" + '",')
        self.assertIn("appsettings-secret", self._types(v))

    def test_json_only_runs_rules_that_declare_it(self) -> None:
        # .json 不在 SCAN_EXTENSIONS: 未声明 exts 的内置规则不应扩散到数据文件
        v = self._scan("csharp.yml", "data.json", '  "note": "TODO fix later",')
        self.assertEqual(v, [])

    def test_mjs_is_scanned(self) -> None:
        v = self._scan("javascript.yml", "tool.mjs", "const f = eval(code)")
        self.assertIn("dangerous-eval", self._types(v))


if __name__ == "__main__":
    unittest.main()
