import os

from waitress import serve

from app import app


if __name__ == "__main__":
    host = os.getenv("BOOKING_CARS_HOST", "127.0.0.1")
    port = int(os.getenv("BOOKING_CARS_PORT", "8000"))
    threads = int(os.getenv("BOOKING_CARS_THREADS", "8"))

    serve(app, host=host, port=port, threads=threads)

