# Demo legacy credit-union admin UI -- the target system discovery/replay
# operate against. Intentionally its own minimal image (fastapi + uvicorn +
# jinja2 only): it must not depend on anything from the automation service.
FROM python:3.11-slim

WORKDIR /app

COPY demo_app/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY demo_app ./demo_app

EXPOSE 8080

CMD ["uvicorn", "demo_app.app:app", "--host", "0.0.0.0", "--port", "8080"]
