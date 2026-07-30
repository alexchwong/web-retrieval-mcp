FROM python:3.12-slim

# trafilatura needs libxml2/libxslt for its lxml backend
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 libxslt1.1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py settings.py settings.toml ./

EXPOSE 8765
CMD ["python", "-u", "server.py"]
