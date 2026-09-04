import json
from typing import Any, Callable, Dict, Optional
import aio_pika
from aio_pika import ExchangeType, Message, DeliveryMode
from pkg.config import get_settings
from pkg.logger import get_logger

logger = get_logger("messaging")
settings = get_settings()


class RabbitMQClient:
    """Asynchronous RabbitMQ client supporting durable exchanges, priority queues, and DLQ."""

    def __init__(self):
        self.connection: Optional[aio_pika.abc.AbstractRobustConnection] = None
        self.channel: Optional[aio_pika.abc.AbstractRobustChannel] = None
        self.exchange: Optional[aio_pika.abc.AbstractRobustExchange] = None
        self.dlx_exchange: Optional[aio_pika.abc.AbstractRobustExchange] = None

    async def connect(self) -> None:
        """Establishes robust connection and sets up CloudTask exchanges and queues."""
        if self.connection and not self.connection.is_closed:
            return

        self.connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
        self.channel = await self.connection.channel()
        await self.channel.set_qos(prefetch_count=settings.WORKER_CONCURRENCY)

        # 1. Dead Letter Exchange (DLX) & Dead Letter Queue (DLQ)
        self.dlx_exchange = await self.channel.declare_exchange(
            name=settings.RABBITMQ_DLX_EXCHANGE,
            type=ExchangeType.TOPIC,
            durable=True,
        )
        dlq_queue = await self.channel.declare_queue(
            name=settings.RABBITMQ_DLQ_QUEUE,
            durable=True,
        )
        await dlq_queue.bind(self.dlx_exchange, routing_key="#")

        # 2. Main Topic Exchange
        self.exchange = await self.channel.declare_exchange(
            name=settings.RABBITMQ_EXCHANGE,
            type=ExchangeType.TOPIC,
            durable=True,
        )

        # 3. Main Task Queue (with dead-letter exchange configuration & max priority 10)
        task_queue = await self.channel.declare_queue(
            name=settings.RABBITMQ_TASK_QUEUE,
            durable=True,
            arguments={
                "x-dead-letter-exchange": settings.RABBITMQ_DLX_EXCHANGE,
                "x-dead-letter-routing-key": "task.dead_lettered",
                "x-max-priority": 10,
            },
        )
        await task_queue.bind(self.exchange, routing_key="task.*")

        # 4. Notification Queue
        notif_queue = await self.channel.declare_queue(
            name=settings.RABBITMQ_NOTIFICATION_QUEUE,
            durable=True,
        )
        await notif_queue.bind(self.exchange, routing_key="notification.*")

        logger.info("RabbitMQ robust connection established, exchanges and queues configured.")

    async def publish_task(
        self,
        task_payload: Dict[str, Any],
        priority: int = 5,
        routing_key: str = "task.created",
    ) -> None:
        """Publishes a persistent task message to RabbitMQ with priority."""
        if not self.exchange:
            await self.connect()

        body = json.dumps(task_payload).encode("utf-8")
        message = Message(
            body=body,
            delivery_mode=DeliveryMode.PERSISTENT,
            priority=min(max(priority, 1), 10),
            content_type="application/json",
            headers={"task_id": str(task_payload.get("id", ""))},
        )
        await self.exchange.publish(message, routing_key=routing_key)
        logger.info(f"Published task {task_payload.get('id')} to {routing_key} with priority {priority}")

    async def publish_event(
        self,
        routing_key: str,
        payload: Dict[str, Any],
    ) -> None:
        """Publishes a general event (e.g., notification, task state change)."""
        if not self.exchange:
            await self.connect()

        body = json.dumps(payload).encode("utf-8")
        message = Message(
            body=body,
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json",
        )
        await self.exchange.publish(message, routing_key=routing_key)

    async def close(self) -> None:
        """Gracefully closes RabbitMQ channel and connection."""
        if self.channel and not self.channel.is_closed:
            await self.channel.close()
        if self.connection and not self.connection.is_closed:
            await self.connection.close()


_rabbitmq_client: Optional[RabbitMQClient] = None


async def get_rabbitmq_client() -> RabbitMQClient:
    """Returns singleton RabbitMQ client."""
    global _rabbitmq_client
    if _rabbitmq_client is None:
        _rabbitmq_client = RabbitMQClient()
        await _rabbitmq_client.connect()
    return _rabbitmq_client


async def check_rabbitmq_health() -> bool:
    """Checks if RabbitMQ connection is active."""
    try:
        client = await get_rabbitmq_client()
        return client.connection is not None and not client.connection.is_closed
    except Exception as e:
        logger.error(f"RabbitMQ health check failed: {e}")
        return False
