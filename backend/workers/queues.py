from redis import Redis
from rq import Queue

from backend.config import get_settings


settings = get_settings()
redis_conn = Redis.from_url(settings.redis_url)
import_queue = Queue("import", connection=redis_conn)
scrape_queue = Queue("scrape", connection=redis_conn)
