FROM python:3.12-slim

ARG BUILD_VERSION=dev
ARG BUILD_ARCH=local
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="addon" io.hass.arch="${BUILD_ARCH}"

WORKDIR /opt/mesh_passes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "app"]
