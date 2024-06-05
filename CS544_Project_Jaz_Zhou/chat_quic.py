from typing import Coroutine,Callable, Optional
from enum import Enum, auto
import asyncio

class ConnectionState(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    AUTHENTICATED = auto()
    SENDING_MESSAGE = auto()
    DISCONNECTING = auto()
    ERROR = auto()

class QuicStreamEvent():
    def __init__(self, stream_id, data, end_stream):
        self.stream_id = stream_id
        self.data = data
        self.end_stream = end_stream
        
class ChatQuicConnection:

    def __init__(self, send, receive, close, new_stream):
        self.send = send
        self.receive = receive
        self.close = close
        self.new_stream = new_stream
        self.state = ConnectionState.DISCONNECTED
        self.previous_state = None
        self.connection_lock = asyncio.Lock()  # Lock to prevent multiple initiations

    async def start_connection(self):
        # print("Attempting to start connection...")
        async with self.connection_lock:
            if self.state == ConnectionState.DISCONNECTED:
                # print("Lock acquired and initiating connection")
                self.update_state(ConnectionState.CONNECTING)
                asyncio.create_task(self.complete_handshake())
                # print("Handshake task started")
            else:
                print(f"Connection already initiated: Current state is {self.state}")


    async def complete_handshake(self):
            try:
                await asyncio.sleep(1)  # Simulate delay for handshake
                self.update_state(ConnectionState.CONNECTED)
            except Exception as e:
                print(f"Handshake failed: {e}")
                self.update_state(ConnectionState.DISCONNECTED)


    def update_state(self, new_state: ConnectionState):
        if self.state != ConnectionState.ERROR:  # Do not overwrite previous state if current is ERROR
            self.previous_state = self.state
        print(f"Transitioning from {self.state} to {new_state}")
        self.state = new_state

    def handle_error(self):
        if self.state != ConnectionState.ERROR:
            self.update_state(ConnectionState.ERROR)
        # Implement recovery or retry logic, or transition to disconnection


    def recover_from_error(self):
        if self.state == ConnectionState.ERROR and self.previous_state:
            self.update_state(self.previous_state)

    def authenticate(self):
        if self.state == ConnectionState.CONNECTED:
            self.update_state(ConnectionState.AUTHENTICATED)
        else:
            print("Cannot authenticate: Connection is not established")

    async def disconnect(self):
        if self.state not in [ConnectionState.DISCONNECTED]:
            self.update_state(ConnectionState.DISCONNECTING)
            self.close()
            self.update_state(ConnectionState.DISCONNECTED)
        else:
            print("Connection already disconnected")

