FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend backend
RUN useradd --uid 10001 --create-home portfolio
USER portfolio
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
