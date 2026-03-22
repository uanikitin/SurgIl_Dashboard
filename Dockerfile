FROM python:3.11-slim

# System dependencies: XeLaTeX for PDF generation
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        texlive-xetex \
        texlive-lang-cyrillic \
        texlive-fonts-recommended \
        texlive-latex-extra \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Generated PDF directory
RUN mkdir -p backend/static/generated/pdf backend/static/generated/temp

EXPOSE 8000

CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
