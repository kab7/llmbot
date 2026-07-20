# Current request examples

These examples reflect the parser schema and deterministic guards in the current
code. Free text is parsed by an LLM, so wording variants can still depend on the
configured model.

## Single chat

```text
Суммаризируй чат Работа за неделю
Что сегодня писали в чате Команда?
Что обсуждали в личке с Иваном за последние 3 часа?
Покажи последние 500 сообщений из чата Release
Какие решения приняли в чате Руководство за 3 дня?
```

Accepted targets include users, groups, supergroups, and channels visible to the
Telethon account.

## Telegram folder

```text
Суммаризируй папку AI за последние сутки
Что нового в папке Проекты сегодня?
Какие риски обсуждали в папке Работа за неделю?
Суммаризируй непрочитанные во всех чатах папки Новости
```

The bot resolves the folder, reproduces its Telegram filter rules, and processes
matched dialogs sequentially. Each chat receives its own result.

## Periods

Last N × 24 hours:

```text
Суммаризируй чат Работа за сутки
Суммаризируй чат Работа за вчера
Суммаризируй чат Работа за 7 дней
Суммаризируй чат Работа за неделю
```

The current parser maps both “вчера” and “за сутки” to `days=1`, meaning the last
24 hours rather than the previous calendar day.

Hours:

```text
Что было в чате Поддержка за последний час?
Что писали в чате Мониторинг за 12 часов?
```

Today:

```text
Что сегодня писали в чате Команда?
```

“Today” starts at local midnight in the bot process timezone.

Message count:

```text
Покажи последние 100 сообщений из чата Проект
```

No explicit period:

```text
Суммаризируй чат Проект
```

The bot inherits the period from context when possible; otherwise it uses the
latest 300 messages.

## Unread and mark-as-read

Read unread messages without changing Telegram state:

```text
Суммаризируй непрочитанные в чате Поддержка
Суммаризируй непрочитанные в папке Работа
```

Explicitly acknowledge processed chats:

```text
Суммаризируй непрочитанные в чате Поддержка и отметь как прочитанные
Суммаризируй папку Новости за сутки и пометь чаты прочитанными
```

`mark_as_read` is discarded unless the original text contains explicit
mark-as-read intent. Merely loading history does not acknowledge a chat.

Current edge case: analysis failures are converted to visible error text by the
LLM helper, so an explicit mark-as-read request can still acknowledge the chat
after an LLM failure. See `docs/AI_DEVELOPMENT.md` before changing this behavior.

## Follow-up context

```text
Суммаризируй чат Работа за неделю
О чем договорились?
Какие следующие шаги?
/context
/reset
```

Context stores only:

- resolved target type;
- resolved target name;
- period type;
- period value.

It does not store the previous question or generated answer. Context is global
for the single admin and is lost on restart.

Explicitly naming a new target replaces the target context. Explicitly naming a
new period replaces the period context.

## Fuzzy lookup

```text
Суммаризируй чат Рабта
Суммаризируй папку Проект
```

Matching ignores case and emoji. Exact matches score 1.0, substring matches 0.9,
and other candidates use `SequenceMatcher`. The best candidate is accepted at
`>= 0.5`, so the bot always shows the recognized target before processing.

## Free-form analysis

```text
Что говорили про дедлайн в чате Проект за неделю?
Кто отвечает за деплой в чате Release?
Какие открытые вопросы остались в папке Проекты?
Были ли противоречия по бюджету в чате Финансы за 3 дня?
```

The processor is instructed to answer only from selected history and to state
when evidence is missing.

## One-request model override

```text
Суммаризируй папку AI с помощью anthropic/claude-opus-4.6
Используй модель openai/gpt-4.1 для анализа чата Release за сутки
```

The override affects only the analysis call. Parsing still uses configured
candidates. The requested model gets three attempts and does not fall back to
the configured model lists.

## Periodic schedules

Daily:

```text
Суммаризируй папку AI каждый день в 20:00
```

Weekly:

```text
Суммаризируй чат Работа каждую неделю в 09:00
```

Monthly:

```text
Суммаризируй папку Отчеты каждый месяц в 10:30
```

Every N days:

```text
Суммаризируй папку Новости раз в 3 дня в 19:30
```

Combined behavior:

```text
Суммаризируй непрочитанные в папке AI каждый день в 20:00 с помощью anthropic/claude-opus-4.6 и отмечай прочитанными
```

A schedule requires explicit recurrence and a valid local `HH:MM`. It is saved
instead of running immediately.

Management:

```text
/schedules
/delschedule ab12cd34
```

## Runtime LLM configuration

Inspect:

```text
/llmconfig
/limits
/limits fallback
```

Primary endpoint/token default scope:

```text
/seturl https://openrouter.ai/api/v1/chat/completions
/settoken <token>
```

Explicit primary/fallback:

```text
/seturl primary https://openrouter.ai/api/v1/chat/completions
/seturl fallback https://openrouter.ai/api/v1/chat/completions
/settoken primary <token>
/settoken fallback <token>
```

Model scope is mandatory:

```text
/setmodel primary meta-llama/llama-3.3-70b-instruct:free,qwen/qwen3-32b:free
/setmodel fallback openrouter/free
```

Changes are applied in memory and persisted to `.env`.

## Provider configuration

OpenRouter-compatible:

```dotenv
PRIMARY_LLM_URL=https://openrouter.ai/api/v1/chat/completions
PRIMARY_LLM_MODEL=meta-llama/llama-3.3-70b-instruct:free
PRIMARY_LLM_API_KEY=...
```

Yandex Cloud:

```dotenv
PRIMARY_LLM_URL=https://ai.api.cloud.yandex.net/v1/chat/completions
PRIMARY_LLM_MODEL=gpt://<folder_id>/<model>
PRIMARY_LLM_API_KEY=...
```

Yandex uses `Api-Key` authorization and does not support the bot's `/limits`
command.

## Operational commands

```text
/start
/help
/folders
/context
/reset
/llmconfig
/limits primary
/seturl primary <url>
/setmodel primary <model>
/settoken primary <token>
/schedules
/delschedule <id>
```
