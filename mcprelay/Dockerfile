FROM python:3.12-alpine
WORKDIR /app
COPY server.py .
ENV PORT=10000
EXPOSE 10000
CMD ["python", "-u", "server.py"]
