FROM python:3.11-slim

# System dependencies: XeLaTeX for PDF generation
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        texlive-xetex \
        texlive-lang-cyrillic \
        texlive-fonts-recommended \
        texlive-latex-extra \
        fonts-liberation \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cached layer — rebuilds only when requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser && \
    mkdir -p backend/static/generated/pdf backend/static/generated/temp && \
    chown -R appuser:appuser backend/static/generated
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
