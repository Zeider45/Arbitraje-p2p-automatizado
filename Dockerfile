FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    ARBITRAJE_DB=/datos/arbitraje.db \
    PANEL_HOST=0.0.0.0 \
    PANEL_PUERTO=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY arbitraje ./arbitraje
COPY config.example.toml ./
VOLUME ["/datos"]
EXPOSE 8000

# Recuerda pasar los secretos como variables de entorno y definir PANEL_CLAVE,
# ya que el panel queda expuesto en 0.0.0.0 dentro del contenedor.
CMD ["python", "-m", "arbitraje", "servir"]
