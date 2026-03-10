"""
SlowAPI Rate Limiter Configuration
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

# Global default 100 requests per minute per IP
limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])
