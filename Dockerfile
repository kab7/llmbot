FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py config.py llm_runtime.py schedule_runtime.py ./

ENV PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]
