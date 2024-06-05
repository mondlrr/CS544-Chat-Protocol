import asyncio
from typing import Dict
import json
from chat_quic import ChatQuicConnection, QuicStreamEvent, ConnectionState
import pdu

def get_supported_versions():
    return [1]  # Add more versions as they become available

async def send_version_negotiation(conn, new_stream_id):
    versions_message = pdu.Datagram(pdu.MSG_TYPE_VERSIONS, json.dumps({"versions": get_supported_versions()}), version=1)
    
    await conn.send(QuicStreamEvent(new_stream_id, versions_message.to_bytes(), False))

# High-level function
async def chat_client_proto(scope: Dict, conn: ChatQuicConnection):
    await conn.start_connection()  # Start the connection properly

    # Loop until the connection is successfully established or an error occurs that cannot be recovered
    while conn.state not in [ConnectionState.CONNECTED, ConnectionState.ERROR]:
        await asyncio.sleep(0.1)  # Polling delay to prevent tight loop

    if conn.state == ConnectionState.CONNECTED:
        new_stream_id = conn.new_stream()
        # Send supported versions first
        await send_version_negotiation(conn, new_stream_id)

        while True:
            username = input("Enter username: ")
            password = input("Enter password: ")
            login_message = pdu.Datagram(pdu.MSG_TYPE_LOGIN, json.dumps({"username": username, "password": password}), version=1)
            await conn.send(QuicStreamEvent(new_stream_id, login_message.to_bytes(), False))

            logout_event = asyncio.Event()
            login_result = await listen_for_messages(conn, logout_event)
            if login_result == "successful":
                break
            if login_result == "unsuccesful_disconnect":
                break

        if login_result == "successful":
            await listen_for_messages(conn, logout_event)

    elif conn.state == ConnectionState.ERROR:
        print("Failed to establish a connection due to an error. Please check the connection settings or network.")
# Listening for messages
async def listen_for_messages(conn: ChatQuicConnection, logout_event: asyncio.Event):
    while True:
        response: QuicStreamEvent = await conn.receive()
        if response:
            response_data = pdu.Datagram.from_bytes(response.data)
            if response_data.msg:
                try:
                    parsed_msg = json.loads(response_data.msg)
                except json.JSONDecodeError as e:
                    print(f"Failed to decode JSON: {e}")
                    continue
            else:
                print("Received empty message")
                continue

            if response_data.mtype == pdu.MSG_TYPE_VERSIONS:
                await handle_version(parsed_msg)
            elif response_data.mtype == pdu.MSG_TYPE_LOGIN_ACK:

                await handle_login_ack(parsed_msg)
                conn.recover_from_error()
                conn.authenticate()
                # Start sending keep-alive messages
                asyncio.ensure_future(send_keep_alive(conn, response.stream_id))
                # Start handling user input
                asyncio.ensure_future(handle_user_input(conn, response.stream_id, logout_event))

                return "successful"

            elif response_data.mtype == pdu.MSG_TYPE_LOGIN_UNSUCCESSFUL_RETRY:
                await handle_unsuccessful(parsed_msg)
                conn.handle_error()
                return "unsuccesful_retry"

            elif response_data.mtype == pdu.MSG_TYPE_LOGIN_UNSUCCESSFUL_DISCONNECT:
                await handle_unsuccessful(parsed_msg)
                conn.handle_error()
                await conn.disconnect()
                return "unsuccesful_disconnect"

            elif response_data.mtype == pdu.MSG_TYPE_MSG_UNSUCCESSFUL:
                conn.handle_error()
                await handle_unsuccessful(parsed_msg)
            elif response_data.mtype == pdu.MSG_TYPE_LOGIN_BROADCAST:
                await handle_broadcast_user_login(parsed_msg)
            elif response_data.mtype == pdu.MSG_TYPE_LOGOUT_BROADCAST:
                await handle_broadcast_user_logout(parsed_msg)
            elif response_data.mtype == pdu.MSG_TYPE_ONE_TO_ONE:
                await handle_one_to_one(parsed_msg)
            elif response_data.mtype == pdu.MSG_TYPE_ONE_TO_MANY:
                await handle_one_to_many(parsed_msg)
            elif response_data.mtype == pdu.MSG_TYPE_BROADCAST:
                await handle_broadcast(parsed_msg)
            elif response_data.mtype == pdu.MSG_TYPE_LOGOUT_ACK:
                await handle_logout_ack(logout_event)
                conn.update_state(ConnectionState.DISCONNECTED)
                return "logout" # Exit the function to end the connection

async def handle_version(parsed_msg):
    version_message = parsed_msg
    print("[Sys] ", version_message)

async def handle_login_failure(conn):
    if conn.state == ConnectionState.CONNECTED:
        conn.update_state(ConnectionState.DISCONNECTING)
        await asyncio.sleep(1)  # Grace period for any last-minute data transmission
        conn.update_state(ConnectionState.DISCONNECTED)
        conn.close()

# Handling user input
async def handle_user_input(conn: ChatQuicConnection, new_stream_id, logout_event: asyncio.Event):
    loop = asyncio.get_running_loop()
    while True:
        user_input = await loop.run_in_executor(None, input)
        if user_input.strip().lower() == "logout":
            conn.update_state(ConnectionState.DISCONNECTING)

            await send_logout_message(conn, new_stream_id)
            await logout_event.wait()
            break  # Exit the loop after logout
        elif user_input.startswith("0:"):  # Broadcast message
            if conn.state != ConnectionState.SENDING_MESSAGE:
                conn.update_state(ConnectionState.SENDING_MESSAGE)

            msg = user_input.split(':', 1)[1].strip()
            if msg:  # Ensure message is not empty
                await send_broadcast_message(conn, new_stream_id, msg)
            else:
                print("Message cannot be empty for broadcast.")
        else:
            try:
                if conn.state != ConnectionState.SENDING_MESSAGE:
                    conn.update_state(ConnectionState.SENDING_MESSAGE)

                target_users, msg = user_input.split(':', 1)
                target_users = target_users.strip()
                msg = msg.strip()
                if not msg:
                    print("Message cannot be empty.")
                    continue
                if ',' in target_users:
                    target_user_ids = target_users.split(',')
                    if all(uid.isdigit() for uid in target_user_ids):  # Check if all user IDs are valid integers
                        await send_one_to_many_message(conn, new_stream_id, target_users, msg)
                    else:
                        print("Invalid user IDs. User IDs must be integers separated by commas.")
                else:
                    if target_users.isdigit():  # Single user ID should be an integer
                        await send_one_to_one_message(conn, new_stream_id, target_users, msg)
                    else:
                        print("Invalid user ID. User ID must be an integer.")
            except ValueError:
                print("Invalid input format. Use 'user_id: message' for direct messages, 'user_id,user_id: message' for one-to-many messages, or '0: message' for broadcast.")


async def send_logout_message(conn: ChatQuicConnection, new_stream_id):
    logout_message = pdu.Datagram(pdu.MSG_TYPE_LOGOUT, "User logging out", version=1)
    await conn.send(QuicStreamEvent(new_stream_id, logout_message.to_bytes(), False))

async def send_one_to_one_message(conn: ChatQuicConnection, new_stream_id, target_user_id, msg):

    chat_message = pdu.Datagram(pdu.MSG_TYPE_ONE_TO_ONE,
                                json.dumps({"target_user_id": target_user_id, "msg": msg}), version=1)
    await conn.send(QuicStreamEvent(new_stream_id, chat_message.to_bytes(), False))

async def send_one_to_many_message(conn, new_stream_id, target_user_ids, msg):
    one_to_many_message = pdu.Datagram(pdu.MSG_TYPE_ONE_TO_MANY,
                                       json.dumps({"target_user_ids": target_user_ids, "msg": msg}), version=1)
    await conn.send(QuicStreamEvent(new_stream_id, one_to_many_message.to_bytes(), False))

async def send_broadcast_message(conn, new_stream_id, msg):
    broadcast_message = pdu.Datagram(pdu.MSG_TYPE_BROADCAST,
                                     json.dumps({"msg": msg}), version=1)
    await conn.send(QuicStreamEvent(new_stream_id, broadcast_message.to_bytes(), False))


# Sending keep-alive messages
async def send_keep_alive(conn: ChatQuicConnection, new_stream_id):
    while True:
        # Include version when creating Datagram
        keep_alive_message = pdu.Datagram(pdu.MSG_TYPE_ALIVE, "keep_alive", version=1)
        await conn.send(QuicStreamEvent(new_stream_id, keep_alive_message.to_bytes(), False))
        await asyncio.sleep(30)  # Send keep-alive message every 30 seconds

# Handlers for different message types
async def handle_login_ack(parsed_msg):
    active_users = parsed_msg
    print("[Sys] Login successful. Active users:", active_users)

async def handle_broadcast_user_login(parsed_msg):
    active_users = parsed_msg
    print("[Sys] Some user login. Active users:", active_users)

async def handle_broadcast_user_logout(parsed_msg):
    active_users = parsed_msg
    print("[Sys] Some user logout. Active users:", active_users)

async def handle_one_to_one(parsed_msg):
    sender_username = parsed_msg['sender_username']
    msg = parsed_msg['msg']
    print(f"[1-1 Msg] {sender_username}: {msg}")

async def handle_one_to_many(parsed_msg):
    sender_username = parsed_msg['sender_username']
    msg = parsed_msg['msg']
    print(f"[1-n Msg] {sender_username}: {msg}")

async def handle_broadcast(parsed_msg):
    sender_username = parsed_msg['sender_username']
    msg = parsed_msg['msg']
    print(f"[Broad Msg] {sender_username}: {msg}")

async def handle_unsuccessful(parsed_msg):
    error_message = parsed_msg

    print(f"[Err] {error_message}")

async def handle_logout_ack(logout_event):
    print("[Sys] Logout successful")
    logout_event.set()  # Signal that logout was successful
