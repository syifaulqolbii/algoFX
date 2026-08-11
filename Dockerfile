FROM python:3.12-slim

WORKDIR /app
COPY python/requirements.txt /app/python/requirements.txt
RUN pip install --no-cache-dir -r /app/python/requirements.txt
COPY python /app/python
COPY prompts /app/prompts

WORKDIR /app/python
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-u", "server.py"]
