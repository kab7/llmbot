FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY bot.py chatgpt_runtime.py config.py schedule_runtime.py telegram_access.py telegram_rpc.py ./
COPY scripts/telegram_auth.py scripts/deploy_check.py scripts/telegram_rpc.py ./scripts/

RUN python -m compileall -q bot.py chatgpt_runtime.py config.py schedule_runtime.py telegram_access.py telegram_rpc.py scripts

CMD ["python", "bot.py"]
