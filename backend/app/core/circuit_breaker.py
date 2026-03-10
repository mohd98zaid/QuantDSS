import time
from enum import Enum
from redis.asyncio import Redis
from app.core.logging import logger

class CircuitState(str, Enum):
    CLOSED = "CLOSED"      # Normal operation
    OPEN = "OPEN"          # API is failing, reject fast
    HALF_OPEN = "HALF_OPEN"# Testing recovery

class CircuitBreaker:
    """Redis-backed distributed circuit breaker for external APIs."""
    
    def __init__(
        self,
        redis: Redis,
        name: str = "broker_api",
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
    ):
        self.redis = redis
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state_key = f"circuit_breaker:{name}:state"
        self._failure_key = f"circuit_breaker:{name}:failures"
        self._last_failure_key = f"circuit_breaker:{name}:last_failure"

    async def get_state(self) -> CircuitState:
        """Get the current circuit breaker state, handling half-open transitions."""
        state_bytes = await self.redis.get(self._state_key)
        if not state_bytes:
            return CircuitState.CLOSED
        
        state = state_bytes.decode("utf-8")
        
        if state == CircuitState.OPEN.value:
            last_failure = await self.redis.get(self._last_failure_key)
            if last_failure:
                time_since_failure = time.time() - float(last_failure.decode("utf-8"))
                if time_since_failure > self.recovery_timeout:
                    await self._set_state(CircuitState.HALF_OPEN)
                    return CircuitState.HALF_OPEN
        
        return CircuitState(state)

    async def _set_state(self, state: CircuitState):
        """Internal method to transition state."""
        await self.redis.set(self._state_key, state.value)
        if state == CircuitState.CLOSED:
            await self.redis.delete(self._failure_key)
        logger.warning(f"[{self.name}] Circuit Breaker transitioned to {state.value}")

    async def record_failure(self):
        """Record an API failure and open circuit if threshold exceeded."""
        failures = await self.redis.incr(self._failure_key)
        # expire failure count after recovery timeout to avoid permanent accumulation
        await self.redis.expire(self._failure_key, self.recovery_timeout * 2)
        await self.redis.set(self._last_failure_key, str(time.time()))
        
        state = await self.get_state()
        if state == CircuitState.HALF_OPEN or failures >= self.failure_threshold:
            if state != CircuitState.OPEN:
                await self._set_state(CircuitState.OPEN)

    async def record_success(self):
        """Record a successful API call, resetting failures or closing circuit."""
        state = await self.get_state()
        if state == CircuitState.HALF_OPEN:
            await self._set_state(CircuitState.CLOSED)
        else:
            # Just reset the failure count
            await self.redis.delete(self._failure_key)
