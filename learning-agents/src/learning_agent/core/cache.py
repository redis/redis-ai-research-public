import functools
import hashlib
import pickle
from typing import Any, Callable, Optional

import redis
from rich import print

from learning_agent.logging.logger import log_event


class RedisCache:
    """Redis cache manager for persistent caching."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
    ):
        """Initialize Redis connection."""
        try:
            self.redis_client = redis.Redis(
                host=host,
                port=port,
                db=db,
                password=password,
                decode_responses=False,  # Keep as False for pickle
            )
            self.redis_client.ping()
            log_event("cache", "redis_connected", {"host": host, "port": port})
        except redis.ConnectionError as e:
            log_event("cache", "redis_connection_error", {"error": e}, success=False)
            raise

    def get(self, key: str) -> Any:
        """Get value from Redis cache."""
        try:
            value = self.redis_client.get(key)
            if value is None:
                return None
            return pickle.loads(value)
        except Exception as e:
            log_event(
                "cache", "redis_get_error", {"error": e, "key": key}, success=False
            )
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set value in Redis cache."""
        try:
            serialized_value = pickle.dumps(value)
            return self.redis_client.set(key, serialized_value, ex=ttl)
        except Exception as e:
            log_event(
                "cache", "redis_set_error", {"error": e, "key": key}, success=False
            )
            return False

    def delete(self, key: str) -> bool:
        """Delete key from Redis cache."""
        try:
            return bool(self.redis_client.delete(key))
        except Exception as e:
            log_event(
                "cache", "redis_delete_error", {"error": e, "key": key}, success=False
            )
            return False

    def delete_pattern(self, pattern: str) -> int:
        """Delete all keys matching a pattern."""
        try:
            keys = self.redis_client.keys(pattern)
            if keys:
                return self.redis_client.delete(*keys)
            return 0
        except Exception as e:
            log_event(
                "cache",
                "redis_delete_pattern_error",
                {"error": e, "pattern": pattern},
                success=False,
            )
            return 0


# Global cache instance
_default_cache = None


def get_default_cache(**redis_kwargs) -> RedisCache:
    """Get or create default Redis cache instance."""
    global _default_cache
    if _default_cache is None:
        _default_cache = RedisCache(**redis_kwargs)
    return _default_cache


def generate_class_method_key(
    instance: Any,
    method_name: str,
    args: tuple,
    kwargs: dict,
    prefix: str = "class_cache",
) -> str:
    """
    Generate cache key for class method calls.

    Args:
        instance: Class instance
        method_name: Method name
        args: Method arguments
        kwargs: Method keyword arguments
        prefix: Cache key prefix
    """
    class_name = instance.__class__.__name__
    module_name = instance.__class__.__module__

    # Create base key from class and method
    base_key = f"{module_name}.{class_name}.{method_name}"

    # Add arguments hash
    try:
        args_hash = hashlib.md5(str(args).encode()).hexdigest()[:8]
        kwargs_hash = hashlib.md5(str(sorted(kwargs.items())).encode()).hexdigest()[:8]
        full_key = f"{prefix}:{base_key}:{args_hash}:{kwargs_hash}"
    except Exception as e:
        # Fallback to simpler key generation
        log_event(
            "cache",
            "generate_class_method_key_error",
            {
                "error": e,
                "instance": instance,
                "method_name": method_name,
                "args": args,
                "kwargs": kwargs,
            },
            success=False,
        )
        full_key = f"{prefix}:{base_key}:{hash((args, tuple(sorted(kwargs.items()))))}"

    return full_key


def redis_cache_method(
    ttl: Optional[int] = None,
    prefix: str = "class_cache",
    cache_instance: Optional[RedisCache] = None,
    ignore_errors: bool = True,
    **redis_kwargs,
) -> Callable:
    """
    Decorator to cache class method results in Redis with persistence across runs.

    Args:
        ttl: Time to live in seconds
        prefix: Prefix for cache keys
        cache_instance: Existing RedisCache instance
        ignore_errors: Whether to ignore cache errors
        **redis_kwargs: Redis connection parameters
    """

    def decorator(method: Callable) -> Callable:
        @functools.wraps(method)
        def wrapper(self, *args, **kwargs):
            # Get cache instance
            if cache_instance:
                cache = cache_instance
            else:
                try:
                    cache = get_default_cache(**redis_kwargs)
                except Exception as e:
                    if ignore_errors:
                        log_event(
                            "cache", "get_default_cache", {"error": e}, success=False
                        )
                        return method(self, *args, **kwargs)
                    else:
                        raise

            # Generate cache key
            cache_key = generate_class_method_key(
                self, method.__name__, args, kwargs, prefix
            )

            # Try to get cached result
            try:
                cached_result = cache.get(cache_key)
                if cached_result is not None:
                    log_event(
                        "cache",
                        "cache_hit",
                        {"cache_key": cache_key, "cached_result": cached_result},
                        success=True,
                    )
                    return cached_result
            except Exception as e:
                if ignore_errors:
                    log_event(
                        "cache",
                        "cache_error",
                        {"error": e, "cache_key": cache_key},
                        success=False,
                    )
                else:
                    raise

            # Cache miss - call method
            log_event(
                "cache",
                "cache_miss",
                {"cache_key": cache_key, "method_name": method.__name__},
                success=True,
            )
            result = method(self, *args, **kwargs)

            # Store result in cache
            try:
                cache.set(cache_key, result, ttl)
                log_event(
                    "cache",
                    "cache_set",
                    {"cache_key": cache_key, "result": result},
                    success=True,
                )
            except Exception as e:
                if ignore_errors:
                    log_event(
                        "cache",
                        "cache_set_error",
                        {"error": e, "cache_key": cache_key},
                        success=False,
                    )
                else:
                    raise

            return result

        # Add cache management methods
        def clear_method_cache(self):
            """Clear all cached results for this method."""
            if cache_instance:
                cache = cache_instance
            else:
                cache = get_default_cache(**redis_kwargs)

            pattern = f"{prefix}:*{self.__class__.__name__}.{method.__name__}*"
            deleted = cache.delete_pattern(pattern)
            log_event(
                "cache",
                "cache_clear_method",
                {"deleted": deleted, "pattern": pattern},
                success=True,
            )
            return deleted

        def clear_instance_cache(self):
            """Clear all cached results for this instance."""
            if cache_instance:
                cache = cache_instance
            else:
                cache = get_default_cache(**redis_kwargs)

            pattern = f"{prefix}:*{self.__class__.__name__}*"

            deleted = cache.delete_pattern(pattern)
            log_event(
                "cache",
                "cache_clear_instance",
                {"deleted": deleted, "pattern": pattern},
                success=True,
            )
            return deleted

        # Attach cache management methods to the wrapper
        wrapper.clear_method_cache = clear_method_cache
        wrapper.clear_instance_cache = clear_instance_cache

        return wrapper

    return decorator


# Alternative approach using class-level caching
class CacheableMixin:
    """Mixin class to add caching capabilities to any class."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cache = get_default_cache()
        self._cache_prefix = f"class_cache:{self.__class__.__name__}"

    def _get_cache_key(self, method_name: str, *args, **kwargs) -> str:
        """Generate cache key for method call."""
        args_str = str(args) + str(sorted(kwargs.items()))
        key_hash = hashlib.md5(args_str.encode()).hexdigest()[:8]
        return f"{self._cache_prefix}:{method_name}:{key_hash}"

    def cached_call(self, method_name: str, ttl: Optional[int] = None, *args, **kwargs):
        """Call a method with caching."""
        cache_key = self._get_cache_key(method_name, *args, **kwargs)

        # Try cache first
        cached_result = self._cache.get(cache_key)
        if cached_result is not None:
            log_event(
                "cache",
                "cache_hit",
                {"cache_key": cache_key, "cached_result": cached_result},
                success=True,
            )
            return cached_result

        # Call method
        method = getattr(self, method_name)
        result = method(*args, **kwargs)

        # Cache result
        self._cache.set(cache_key, result, ttl)
        log_event(
            "cache",
            "cache_set",
            {"cache_key": cache_key, "result": result},
            success=True,
        )

        return result

    def clear_cache(self, method_name: Optional[str] = None):
        """Clear cache for specific method or all methods."""
        if method_name:
            pattern = f"{self._cache_prefix}:{method_name}:*"
        else:
            pattern = f"{self._cache_prefix}:*"

        return self._cache.delete_pattern(pattern)


def time_runs(func, *args, **kwargs):
    start_time = time.time()
    func(*args, **kwargs)
    end_time = time.time()
    print(f"Time taken: {end_time - start_time} seconds")


# Example usage
if __name__ == "__main__":
    import time

    # Example 1: Using decorator approach
    class DataProcessor:
        def __init__(self, config_id: str):
            self.config_id = config_id
            self.multiplier = 2

        @redis_cache_method(ttl=3600, prefix="data_processor")
        def expensive_computation(self, data: list) -> float:
            """Simulate expensive computation."""
            print(f"Processing data with config {self.config_id}...")
            time.sleep(1)  # Simulate work
            return sum(data) * self.multiplier

        @redis_cache_method(ttl=1800, prefix="data_processor")
        def instance_specific_computation(self, value: int) -> int:
            """Computation that depends on instance state."""
            print(f"Instance-specific computation for {self.config_id}...")
            time.sleep(0.5)
            return value * self.multiplier + hash(self.config_id) % 100

    # Example 2: Using mixin approach
    class ApiClient(CacheableMixin):
        def __init__(self, base_url: str):
            super().__init__()
            self.base_url = base_url

        def fetch_data(self, endpoint: str) -> dict:
            """Simulate API call."""
            print(f"Fetching data from {self.base_url}{endpoint}...")
            time.sleep(0.3)
            return {"data": f"Response from {endpoint}", "timestamp": time.time()}

    # Test the caching
    print("Testing persistent class method caching:")
    print("=" * 50)

    # Test DataProcessor
    processor1 = DataProcessor("config_A")
    processor2 = DataProcessor("config_B")

    print("First run (should be slow):")
    time_runs(processor1.expensive_computation, [1, 2, 3, 4, 5])

    print("\nSecond run (should be fast - cached):")
    time_runs(processor1.expensive_computation, [1, 2, 3, 4, 5])

    print("\nDifferent instance, same parameters:")
    time_runs(processor2.expensive_computation, [1, 2, 3, 4, 5])

    print("\nInstance-independent caching:")
    time_runs(processor1.instance_specific_computation, 10)

    time_runs(processor1.instance_specific_computation, 10)  # Should be cached

    # Test cache clearing
    print("\n" + "=" * 50)
    print("Testing cache clearing:")

    # Clear specific method cache
    # cleared = processor1.expensive_computation.clear_method_cache(processor1)
    print("Cleared entries for expensive_computation")

    # Clear instance cache
    # cleared = processor1.instance_specific_computation.clear_instance_cache(processor1)
    print("Cleared entries for instance")

    print("\nCache test complete!")
    print(
        "Note: Cache persists across program runs - restart this script to see cached results!"
    )
