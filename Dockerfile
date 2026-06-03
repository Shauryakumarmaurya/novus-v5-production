# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Suppress missing WeasyPrint warnings if it tries to load wrong lib, though apt install fixes it
ENV WEASYPRINT_LOG_LEVEL=ERROR

# Set work directory
WORKDIR /app

# Install OS dependencies for WeasyPrint and others
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /app/
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy the rest of the application codebase
COPY . /app/

# Expose port 5001 for the Flask web application
EXPOSE 5001

# Note: The actual command to run (web or worker) will be defined in docker-compose.yml
