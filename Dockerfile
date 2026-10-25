FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir openpyxl
COPY . .
ENV HOST=0.0.0.0 PORT=8765
EXPOSE 8765
CMD ["python", "app.py", "--no-browser"]
