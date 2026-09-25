FROM python:3.12-slim
WORKDIR /app
COPY relay/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY relay /app/relay
ENV ANSX_RELAY_DATA=/data
VOLUME /data
EXPOSE 8000
# One worker: rate limits are in-process. SQLite (WAL) + /data volume hold all state.
CMD ["uvicorn", "relay.server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--ws", "none"]
