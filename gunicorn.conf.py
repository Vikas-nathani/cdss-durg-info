workers = 4
worker_class = "uvicorn.workers.UvicornWorker"
bind = "0.0.0.0:8000"
timeout = 120
keepalive = 5
accesslog = "logs/access.log"
errorlog = "logs/error.log"
loglevel = "info"
