FROM python:3.12-slim
WORKDIR /app
# requirements.txt states what the viewer needs; requirements.lock pins every
# package (scripts/lock.sh), so the same commit builds the same image any day.
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -c requirements.lock -r requirements.txt
COPY app.py .
EXPOSE 8501
ENV REPORTS_DB=/data/reports.duckdb
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
