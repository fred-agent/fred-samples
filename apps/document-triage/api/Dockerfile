FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./

# A numeric uid, not a name: a cluster enforcing runAsNonRoot cannot verify a
# named user and refuses to start the container.
RUN useradd --uid 10001 --create-home appuser
USER 10001

# No volume and no database: this service keeps nothing of its own. Every
# record lives in the team's Knowledge Flow workspace, written as the user.
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
