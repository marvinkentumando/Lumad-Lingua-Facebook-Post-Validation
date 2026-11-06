FROM python:3.12-slim
WORKDIR /app
COPY server.py /app/server.py
COPY static /app/static
COPY data /app/data
ENV PYTHONUNBUFFERED=1
ENV LL_HOST=0.0.0.0
ENV LL_DATA_DIR=/app/.data
EXPOSE 8000
CMD ["python", "server.py"]
