FROM python:3.12-slim

WORKDIR /app

# Install Python deps first for layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app source.
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini .

ENV PYTHONPATH=/app/src
ENV PYTHONIOENCODING=utf-8

EXPOSE 8000

CMD ["python", "-m", "api.main"]
