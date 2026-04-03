import os
import logging
import time
import json
import threading
import queue

import redis
import requests

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

class AnalyticsWorker:
    """
    A worker that processes analytics data from a queue,
    aggregates it, and sends it to a reporting service.
    """

    def __init__(self, redis_host='localhost', redis_port=6379,
                 queue_name='analytics_queue',
                 reporting_url='http://localhost:8000/report',
                 batch_size=10, aggregation_interval=60):
        """
        Initializes the AnalyticsWorker.

        Args:
            redis_host (str): The host of the Redis server.
            redis_port (int): The port of the Redis server.
            queue_name (str): The name of the Redis queue.
            reporting_url (str): The URL of the reporting service.
            batch_size (int): The number of events to process in a batch.
            aggregation_interval (int): The interval (in seconds) to aggregate data.
        """
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.queue_name = queue_name
        self.reporting_url = reporting_url
        self.batch_size = batch_size
        self.aggregation_interval = aggregation_interval
        self.redis_client = redis.Redis(host=self.redis_host, port=self.redis_port)
        self.data_buffer = []
        self.stop_event = threading.Event()
        self.aggregation_lock = threading.Lock()
        self.data_queue = queue.Queue()

    def start(self):
        """
        Starts the worker.
        """
        logging.info("Analytics worker started.")
        self.consumer_thread = threading.Thread(target=self._consume_data)
        self.consumer_thread.daemon = True
        self.consumer_thread.start()

        self.aggregator_thread = threading.Thread(target=self._aggregate_and_report)
        self.aggregator_thread.daemon = True
        self.aggregator_thread.start()

    def stop(self):
        """
        Stops the worker.
        """
        logging.info("Analytics worker stopping...")
        self.stop_event.set()
        self.consumer_thread.join()
        self.aggregator_thread.join()
        logging.info("Analytics worker stopped.")

    def _consume_data(self):
        """
        Consumes data from the Redis queue.
        """
        while not self.stop_event.is_set():
            try:
                item = self.redis_client.blpop(self.queue_name, timeout=5)
                if item:
                    _, data = item
                    try:
                        event = json.loads(data.decode('utf-8'))
                        self.data_queue.put(event)
                    except json.JSONDecodeError:
                        logging.error(f"Invalid JSON data: {data}")
                    except Exception as e:
                        logging.error(f"Error processing data: {e}")
            except redis.exceptions.ConnectionError as e:
                logging.error(f"Redis connection error: {e}")
                time.sleep(5)  # Wait before retrying

    def _aggregate_and_report(self):
        """
        Aggregates data and sends it to the reporting service.
        """
        while not self.stop_event.is_set():
            time.sleep(self.aggregation_interval)
            with self.aggregation_lock:
                # Drain the queue into the buffer
                while not self.data_queue.empty():
                    try:
                        event = self.data_queue.get(timeout=0.1)
                        self.data_buffer.append(event)
                        self.data_queue.task_done()
                    except queue.Empty:
                        break # Queue is empty.

                if self.data_buffer:
                    aggregated_data = self._aggregate_data(self.data_buffer)
                    self._send_report(aggregated_data)
                    self.data_buffer = []  # Clear the buffer

    def _aggregate_data(self, data):
        """
        Aggregates the given data.

        Args:
            data (list): A list of data events.

        Returns:
            dict: The aggregated data.
        """
        # Example aggregation: Count events by type
        event_counts = {}
        for event in data:
            event_type = event.get('type', 'unknown')
            event_counts[event_type] = event_counts.get(event_type, 0) + 1

        return {'event_counts': event_counts}

    def _send_report(self, data):
        """
        Sends the report to the reporting service.

        Args:
            data (dict): The data to send.
        """
        try:
            response = requests.post(self.reporting_url, json=data)
            response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
            logging.info(f"Report sent successfully. Status code: {response.status_code}")
        except requests.exceptions.RequestException as e:
            logging.error(f"Error sending report: {e}")

if __name__ == "__main__":
    redis_host = os.environ.get('REDIS_HOST', 'localhost')
    redis_port = int(os.environ.get('REDIS_PORT', 6379))
    queue_name = os.environ.get('ANALYTICS_QUEUE', 'analytics_queue')
    reporting_url = os.environ.get('REPORTING_URL', 'http://localhost:8000/report')
    batch_size = int(os.environ.get('BATCH_SIZE', 10))
    aggregation_interval = int(os.environ.get('AGGREGATION_INTERVAL', 60))

    worker = AnalyticsWorker(redis_host=redis_host, redis_port=redis_port,
                             queue_name=queue_name, reporting_url=reporting_url,
                             batch_size=batch_size, aggregation_interval=aggregation_interval)

    try:
        worker.start()
        while True:
            time.sleep(1) # Keep the main thread alive
    except KeyboardInterrupt:
        worker.stop()