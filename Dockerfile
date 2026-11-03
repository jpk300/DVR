FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py gunicorn.conf.py ./
COPY dvr dvr
COPY static static
RUN mkdir -p /data
EXPOSE 8080
# Bind address, worker count and the scheduler start-up hook are in gunicorn.conf.py
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
