FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /usr/local/share/ca-certificates/russian-trusted \
    && curl -kfsSL --retry 3 https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt \
       -o /usr/local/share/ca-certificates/russian-trusted/russian_trusted_root_ca.crt \
    && curl -kfsSL --retry 3 https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt \
       -o /usr/local/share/ca-certificates/russian-trusted/russian_trusted_sub_ca.crt \
    && update-ca-certificates

WORKDIR /app
COPY maxbot-selfhosted.py /app/maxbot-selfhosted.py
COPY maxbot-entry.py /app/maxbot-entry.py
RUN mkdir -p /app/data

CMD ["python", "/app/maxbot-entry.py"]
