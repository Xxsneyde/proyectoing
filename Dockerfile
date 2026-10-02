# Versión de prueba web (sin IA) para cualquier hosting con Docker.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m -u 1000 erp && chown -R erp /app
USER erp
EXPOSE 8000
CMD ["python", "servidor_erp.py", "--nube"]
