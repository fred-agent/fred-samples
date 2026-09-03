FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./

# A numeric uid, not a name: a cluster enforcing runAsNonRoot cannot verify a
# named user and refuses to start the container.
RUN useradd --uid 10001 --create-home appuser

# The SQLite file lives on a mount, not in the image layer. Owned by the
# runtime uid so a read-only-root or non-root pod can still write it.
ENV PROGRESS_TRACKER_DB=/data/progress-tracker.db
RUN mkdir -p /data && chown 10001:10001 /data
VOLUME ["/data"]

USER 10001

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
