FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY bot.py config.py llm_runtime.py schedule_runtime.py ./

RUN python -m compileall -q bot.py config.py llm_runtime.py schedule_runtime.py

CMD ["python", "bot.py"]
