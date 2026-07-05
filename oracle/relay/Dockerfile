FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# src/ = 배포 스테이징에서 채워지는 executor 패키지 트리
# (features/strategy/polymarket/_data/{executor,live}.py + __init__ 체인)
COPY src /app/src
COPY app.py /app/app.py

EXPOSE 9090
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9090"]
