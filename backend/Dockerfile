FROM python:3.13-slim

# One maths-library thread: by default NumPy/SciPy's OpenBLAS sets up a thread and
# buffer per host CPU (847 MB reserved at startup on 12 CPUs, 139 MB with one), and
# a container can see many host CPUs. The backend's numeric work is small.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --requirement requirements.txt

COPY app ./app
RUN mkdir -p /data

EXPOSE 8000
STOPSIGNAL SIGTERM

CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
