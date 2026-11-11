# Frontend: nginx serves the static UI and forwards /api to the FastAPI backend.
# No build step: the files are copied as they are.
# Build context is ./frontend (see compose.yaml).
FROM nginx:1.28-alpine

RUN rm -rf /usr/share/nginx/html/*
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html /usr/share/nginx/html/
COPY css /usr/share/nginx/html/css
COPY js /usr/share/nginx/html/js
COPY templates /usr/share/nginx/html/templates

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q --spider http://127.0.0.1/ || exit 1
