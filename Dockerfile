FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Demo pública: datos ficticios, cuentas con contraseñas públicas y base de datos en memoria.
ENV NPLDESK_DEMO=1 \
    NPLDESK_DB=:memory: \
    HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["python", "-m", "npldesk.api"]
