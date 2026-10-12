FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py ./
COPY static ./static
COPY templates ./templates
RUN mkdir -p /app/data /app/secrets
EXPOSE 8060
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8060"]
