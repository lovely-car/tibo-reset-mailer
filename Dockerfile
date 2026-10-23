FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY tibo_mailer ./tibo_mailer
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home mailer && mkdir /data && chown mailer:mailer /data
USER mailer
ENV PYTHONUNBUFFERED=1 TIBO_DATA_DIR=/data
VOLUME ["/data"]
CMD ["python", "-m", "tibo_mailer", "run"]
