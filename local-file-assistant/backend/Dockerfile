FROM python:3.12-slim

# libgl1 + libglib2.0-0: needed by OpenCV, which RapidOCR pulls in
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    XDG_DATA_HOME=/data \
    LFA_IN_DOCKER=1 \
    HOST=0.0.0.0 \
    PORT=8756

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY run.py .

EXPOSE 8756
CMD ["python", "run.py"]
