import ast
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import unquote

import bot
import schedule_runtime


ROOT = Path(__file__).resolve().parents[1]
PARSER_SCHEMA_PATH = ROOT / "docs/schemas/parser-command.schema.json"
SCHEDULE_SCHEMA_PATH = ROOT / "docs/schemas/schedule-record.schema.json"

EXPECTED_COMMANDS = {
    "start",
    "help",
    "folders",
    "context",
    "reset",
    "llmconfig",
    "limits",
    "seturl",
    "setmodel",
    "settoken",
    "schedules",
    "delschedule",
}

EXPECTED_ENV_KEYS = {
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_API_ID",
    "TELEGRAM_API_HASH",
    "TELEGRAM_PHONE",
    "PRIMARY_LLM_URL",
    "PRIMARY_LLM_MODEL",
    "PRIMARY_LLM_API_KEY",
    "FALLBACK_LLM_URL",
    "FALLBACK_LLM_MODEL",
    "FALLBACK_LLM_TOKEN",
    "LLM_REQUEST_TIMEOUT_SECONDS",
    "LLM_MAX_RETRIES",
    "PRIMARY_FREE_MODEL_INTERVAL_SECONDS",
    "PRIMARY_FREE_MODEL_429_BACKOFF_SECONDS",
    "PRIMARY_FREE_MODEL_429_BACKOFF_STEP_SECONDS",
    "FALLBACK_FREE_MODEL_INTERVAL_SECONDS",
    "FALLBACK_FREE_MODEL_429_BACKOFF_SECONDS",
    "FALLBACK_FREE_MODEL_429_BACKOFF_STEP_SECONDS",
    "LOG_FILE_PATH",
    "LOG_MAX_BYTES",
    "LOG_BACKUP_COUNT",
    "LLM_TRAFFIC_LOG_PATH",
    "LLM_TRAFFIC_LOG_MAX_BYTES",
    "LLM_TRAFFIC_LOG_BACKUP_COUNT",
    "SESSION_NAME",
    "SCHEDULES_FILE",
    "ADMIN_USER_ID",
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _registered_commands() -> set[str]:
    tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
    commands: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "CommandHandler":
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(
                first_arg.value, str
            ):
                commands.add(first_arg.value)
    return commands


def _env_example_keys() -> set[str]:
    result = set()
    for line in (ROOT / "env.example").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
        if match:
            result.add(match.group(1))
    return result


def _runtime_env_keys() -> set[str]:
    result: set[str] = set()
    for relative_path in ("config.py", "bot.py"):
        tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            function_name = None
            if isinstance(node.func, ast.Name):
                function_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                function_name = node.func.attr
            if function_name not in {"getenv", "_parse_int_env"}:
                continue
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(
                first_arg.value, str
            ):
                result.add(first_arg.value)
    return result


def test_parser_schema_matches_runtime_contract():
    schema = _load_json(PARSER_SCHEMA_PATH)
    properties = schema["properties"]

    assert set(schema["required"]) == set(properties)
    assert set(properties["target_type"]["enum"]) == bot.ALLOWED_TARGET_TYPES
    assert set(properties["folder_mode"]["enum"]) == bot.ALLOWED_FOLDER_MODES
    assert set(properties["period_type"]["enum"]) == bot.ALLOWED_PERIOD_TYPES
    assert set(properties["recurrence_type"]["enum"]) == {
        "daily",
        "weekly",
        "monthly",
        "interval_days",
        None,
    }

    for field in properties:
        assert f'"{field}"' in bot.config.PARSER_PROMPT


def test_schedule_schema_matches_python_and_sqlite(tmp_path: Path):
    schema = _load_json(SCHEDULE_SCHEMA_PATH)
    expected_columns = schedule_runtime._SCHEDULE_COLUMNS

    assert schema["required"] == expected_columns
    assert list(schema["properties"]) == expected_columns
    assert set(schema["properties"]["period_type"]["enum"]) == (
        bot.ALLOWED_PERIOD_TYPES - {None}
    ) | {None}

    db_path = tmp_path / "schema.db"
    schedule_runtime.load_schedules(db_path)
    with sqlite3.connect(db_path) as conn:
        actual_columns = [
            row[1] for row in conn.execute("PRAGMA table_info(schedules)").fetchall()
        ]
    assert actual_columns == expected_columns


def test_documented_commands_match_registered_handlers():
    assert _registered_commands() == EXPECTED_COMMANDS

    for relative_path in ("README.md", "docs/QUICKSTART.md"):
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        for command in EXPECTED_COMMANDS:
            assert f"/{command}" in content, f"/{command} missing from {relative_path}"


def test_env_example_is_complete():
    assert _env_example_keys() == EXPECTED_ENV_KEYS
    assert _runtime_env_keys() == EXPECTED_ENV_KEYS


def test_dockerfile_copies_every_runtime_module():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    for module in ("bot.py", "config.py", "llm_runtime.py", "schedule_runtime.py"):
        assert module in dockerfile


def test_internal_markdown_links_resolve():
    markdown_files = [
        ROOT / "README.md",
        ROOT / "AGENTS.md",
        ROOT / "CLAUDE.md",
        *(ROOT / "docs").glob("*.md"),
    ]
    link_pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

    for markdown_file in markdown_files:
        content = markdown_file.read_text(encoding="utf-8")
        for raw_target in link_pattern.findall(content):
            target = raw_target.strip().split("#", 1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (markdown_file.parent / unquote(target)).resolve()
            assert resolved.exists(), (
                f"Broken link in {markdown_file.relative_to(ROOT)}: {raw_target}"
            )
