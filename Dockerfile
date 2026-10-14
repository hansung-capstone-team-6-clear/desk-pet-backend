FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

# SQLite 파일(data/desk_pet.db)을 둘 폴더. docker-compose의 named volume이 여기에 붙는다.
RUN useradd --create-home appuser && mkdir -p /app/data && chown appuser /app/data
USER appuser

EXPOSE 8000

CMD ["uvicorn", "desk_pet_backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
