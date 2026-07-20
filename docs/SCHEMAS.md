# Machine-readable contracts

The application code remains the source of truth. These schemas are derived
artifacts for agents, reviews, fixtures, and external tooling:

- [`schemas/parser-command.schema.json`](schemas/parser-command.schema.json)
  describes the exact ten fields requested by `config.PARSER_PROMPT`.
  `validate_command_payload()` normalizes that object and adds the internal
  `time_missing` field.
- [`schemas/schedule-record.schema.json`](schemas/schedule-record.schema.json)
  describes records produced by `build_schedule_record()` and stored in the
  `schedules` SQLite table.

`tests/test_repository_contracts.py` compares schema fields and enum values with
the Python constants and the real SQLite table. Update code first, then schemas,
tests, and documentation in the same change.

The schemas intentionally describe canonical generated records. They do not
replace runtime validation and are not loaded by the bot.
