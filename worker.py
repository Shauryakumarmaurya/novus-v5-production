from utils.logger import get_logger
logger = get_logger(__name__)
import os
# Fix for macOS fork() issue with CoreFoundation/Objective-C
os.environ['OBJC_DISABLE_INITIALIZE_FORK_SAFETY'] = 'YES'
# Prevent huggingface tokenizers from forking
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
# Prevent curl/urllib proxy resolution from crashing CoreFoundation on fork
os.environ['no_proxy'] = '*'

from pathlib import Path
from dotenv import load_dotenv
from rq import SimpleWorker
from redis_config import get_queue, get_redis

if __name__ == "__main__":
    # Load .env so worker matches app environment
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(env_path)
    # Sanity check Redis connectivity
    get_redis().ping()
    logger.info("[Worker] Connected to Redis. Starting daemon worker...")
    worker = SimpleWorker([get_queue()])
    worker.work()