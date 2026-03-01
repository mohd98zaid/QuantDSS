FROM python:3.12-slim

WORKDIR /app

# Install Python dependencies (no C compilation needed — pure Python TA library)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
