FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY app.py .

# Expose port
EXPOSE 8080

# Default: monitor /var/log/datasystem
# Override with: docker run -v /my/logs:/var/log/datasystem -p 8080:8080 datasystem-log-analyzer
CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "8080"]
