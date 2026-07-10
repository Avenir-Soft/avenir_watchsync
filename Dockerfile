FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir fastapi "uvicorn[standard]"

COPY server.py watch-sync-kiwi.zip ./

ENV WATCH_DB=/data/watch.db
EXPOSE 8765

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8765"]
