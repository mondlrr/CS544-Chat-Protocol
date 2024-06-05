import asyncio
from typing import Dict
import json
from chat_quic import ChatQuicConnection, QuicStreamEvent, ConnectionState
import pdu
from user_db import user_db  # Import the user database

def get_supported_versions():
    return [1]  # Add more versions as they become available

async def choose_compatible_version(client_versions, conn, stream_id):
    common_versions = set(client_versions).intersection(get_supported_versions())
    if common_versions:
        selected_version = max(common_versions)  # Select the highest compatible version
        await send_response(conn, stream_id, pdu.MSG_TYPE_VERSIONS, json.dumps({"selected_version": selected_version}), version=1)
        return selected_version
    else:
        await send_response(conn, stream_id, pdu.MSG_TYPE_VERSIONS, json.dumps({"error": "No compatible version"}), version=1)
        return None

active_user_connections = {}

async def chat_server_proto(scope: Dict, conn: ChatQuicConnection):
    if conn.state == ConnectionState.DISCONNECTED:
        await conn.start_connection()

    while conn.state not in [ConnectionState.DISCONNECTED, ConnectionState.ERROR]:
        try:
            message: QuicStreamEvent = await conn.receive()
            if message:

                # print(f"[svr] Message received: {message.data}")
                dgram_in = pdu.Datagram.from_bytes(message.data)
                # print(f"[svr] Processing message of type {dgram_in.mtype}.")


                if dgram_in.mtype == pdu.MSG_TYPE_VERSIONS:
                    client_versions = json.loads(dgram_in.msg)['versions']
                    selected_version = await choose_compatible_version(client_versions, conn, message.stream_id)
                    if not selected_version:
                        break  # End connection if no compatible version found
                    print("Negotiate version successful on version ", selected_version)

                elif dgram_in.mtype == pdu.MSG_TYPE_LOGIN:
                    user_id = await handle_login(dgram_in, conn, message)
                    if user_id:
                        conn.recover_from_error()
                        conn.authenticate()
                    else:
                        await conn.disconnect()

                elif dgram_in.mtype == pdu.MSG_TYPE_ONE_TO_ONE:
                    await handle_one_to_one(dgram_in, conn, message, user_id)
                    if conn.state != ConnectionState.SENDING_MESSAGE:
                        conn.update_state(ConnectionState.SENDING_MESSAGE)


                elif dgram_in.mtype == pdu.MSG_TYPE_ONE_TO_MANY:
                    await handle_one_to_many(dgram_in, conn, message, user_id)
                    if conn.state != ConnectionState.SENDING_MESSAGE:
                        conn.update_state(ConnectionState.SENDING_MESSAGE)


                elif dgram_in.mtype == pdu.MSG_TYPE_BROADCAST:
                    await handle_broadcast_message(dgram_in, conn, message, user_id)
                    if conn.state != ConnectionState.SENDING_MESSAGE:
                        conn.update_state(ConnectionState.SENDING_MESSAGE)


                elif dgram_in.mtype == pdu.MSG_TYPE_LOGOUT:
                    if await handle_logout(conn, message, user_id):
                        break  # Exit the loop to end the connection

                elif dgram_in.mtype == pdu.MSG_TYPE_ALIVE:
                    await handle_keep_alive(user_id)

                else:
                    print("[svr] Unknown message type")
        except Exception as e:
            print(f"Error processing message: {e}")
            break

    print("[Server] Connection closed or error occurred.")


MAX_LOGIN_ATTEMPTS = 3  # Maximum number of allowed login attempts

async def handle_login(dgram_in, conn, message):
    attempt_count = 0
    while attempt_count < MAX_LOGIN_ATTEMPTS:
        try:
            credentials = json.loads(dgram_in.msg)
            username = credentials['username']
            password = credentials['password']

            if await is_user_authenticated(username, password):
                if await is_user_already_logged_in(username):
                    attempt_count += 1
                    if attempt_count < MAX_LOGIN_ATTEMPTS:
                        await send_login_retry(conn, message.stream_id,
                                               f"User already logged in. Attempts left: {MAX_LOGIN_ATTEMPTS - attempt_count}")
                    else:
                        await send_login_failure(conn, message.stream_id, "Maximum login attempts exceeded.")

                    conn.handle_error()
                    new_message = await asyncio.wait_for(conn.receive(), timeout=60)
                    dgram_in = pdu.Datagram.from_bytes(new_message.data)

                else:
                    user_id = user_db.generate_unique_user_id()
                    user_db.add_active_user(user_id, username)
                    await broadcast_active_users(True)
                    active_users = user_db.get_active_users()
                    active_user_connections[user_id] = (conn, message.stream_id)
                    await send_response(conn, message.stream_id, pdu.MSG_TYPE_LOGIN_ACK, json.dumps(active_users))
                    return user_id
            else:
                attempt_count += 1
                if attempt_count < MAX_LOGIN_ATTEMPTS:
                    await send_login_retry(conn, message.stream_id,
                                           f"Invalid credentials. Attempts left: {MAX_LOGIN_ATTEMPTS - attempt_count}")
                else:
                    await send_login_failure(conn, message.stream_id, "Maximum login attempts exceeded.")

                conn.handle_error()
                new_message = await asyncio.wait_for(conn.receive(), timeout=60)
                dgram_in = pdu.Datagram.from_bytes(new_message.data)


        except json.JSONDecodeError:
            await send_login_failure(conn, message.stream_id, "Malformed data received")
            await handle_login_failure(conn, message.stream_id)
            conn.handle_error()
            return None
        except asyncio.TimeoutError:
            await send_login_failure(conn, message.stream_id, "Login timeout. Please try again.")
            await handle_login_failure(conn, message.stream_id)
            conn.handle_error()
            return None
        except Exception as e:
            await send_login_failure(conn, message.stream_id, f"Error during login: {str(e)}")
            await handle_login_failure(conn, message.stream_id)
            conn.handle_error()
            return None

    return None

async def send_login_retry(conn, stream_id, error_message):
    await send_response(conn, stream_id, pdu.MSG_TYPE_LOGIN_UNSUCCESSFUL_RETRY, json.dumps({"error": error_message}))

async def send_login_failure(conn, stream_id, error_message):
    await send_response(conn, stream_id, pdu.MSG_TYPE_LOGIN_UNSUCCESSFUL_DISCONNECT, json.dumps({"error": error_message}))



async def send_login_success(conn, stream_id):
    active_users = user_db.get_active_users()
    await send_response(conn, stream_id, pdu.MSG_TYPE_LOGIN_ACK, json.dumps(active_users))


async def handle_login_failure(conn, message):
    if conn.state == ConnectionState.CONNECTED:
        conn.update_state(ConnectionState.DISCONNECTING)
        await asyncio.sleep(1)  # Grace period for sending the last message
        conn.update_state(ConnectionState.DISCONNECTED)
        await conn.close()

async def handle_one_to_one(dgram_in, conn, message, user_id):
    if user_id is None:
        await send_response(conn, message.stream_id, pdu.MSG_TYPE_MSG_UNSUCCESSFUL, json.dumps({"error": "User not authenticated"}))
        return

    message_content = json.loads(dgram_in.msg)
    message_type = dgram_in.mtype
    target_user_id = int(message_content['target_user_id'])
    msg = message_content['msg']

    if target_user_id in user_db.active_users:
        await send_message_to_target_user(conn, message_type, message, target_user_id, user_id, msg)
    else:
        await send_unsuccessful_message_to_sender(conn, message, target_user_id)

async def handle_one_to_many(dgram_in, conn, message, user_id):
    if user_id is None:
        await send_response(conn, message.stream_id, pdu.MSG_TYPE_MSG_UNSUCCESSFUL, json.dumps({"error": "User not authenticated"}))
        return

    message_content = json.loads(dgram_in.msg)
    message_type = dgram_in.mtype
    target_user_ids = [int(uid) for uid in message_content['target_user_ids'].split(',')]
    msg = message_content['msg']

    for target_user_id in target_user_ids:
        if target_user_id in user_db.active_users:
            await send_message_to_target_user(conn, message_type, message, target_user_id, user_id, msg)
        else:
            await send_unsuccessful_message_to_sender(conn, message, target_user_id)

async def handle_broadcast_message(dgram_in, conn, message, user_id):
    if user_id is None:
        await send_response(conn, message.stream_id, pdu.MSG_TYPE_MSG_UNSUCCESSFUL, json.dumps({"error": "User not authenticated"}))
        return

    message_content = json.loads(dgram_in.msg)
    message_type = dgram_in.mtype
    msg = message_content['msg']
    for target_user_id in user_db.active_users.keys():
        await send_message_to_target_user(conn, message_type, message, target_user_id, user_id, msg)


async def handle_logout(conn, message, user_id):
    if user_id is not None and user_id in active_user_connections:
        print(user_id, " logout")
        # Set state to DISCONNECTING
        conn.update_state(ConnectionState.DISCONNECTING)
        # Remove user from active connections and perform cleanup
        del active_user_connections[user_id]
        user_db.remove_active_user(user_id)
        # Broadcast the updated list of active users
        await broadcast_active_users(False)
        # Notify client of successful logout
        await send_response(conn, message.stream_id, pdu.MSG_TYPE_LOGOUT_ACK, json.dumps({"sys": "Logout successful"}))
        # Fully disconnect after cleanup
        conn.update_state(ConnectionState.DISCONNECTED)
        return True
    return False

async def handle_keep_alive(user_id):
    print(user_id, " keep alive")

# Response Sending Functions
async def send_response(conn, stream_id, message_type, message, version=1):
    response = pdu.Datagram(message_type, message, version)
    await conn.send(QuicStreamEvent(stream_id, response.to_bytes(), False))


async def send_message_to_target_user(conn, message_type, message, target_user_id, user_id, msg, version=1):
    target_user_name = user_db.get_username(target_user_id)
    target_conn, stream_id = active_user_connections.get(target_user_id)  # Get the connection for the target user
    if target_conn is not None:
        sender_username = user_db.get_username(user_id)
        forward_message = pdu.Datagram(message_type,
                                       json.dumps({"sender_user_id": user_id,
                                                   "sender_username": sender_username,
                                                   "msg": msg}), version)
        print("send to ", target_user_name)
        await target_conn.send(QuicStreamEvent(stream_id, forward_message.to_bytes(), False))
    else:
        print("unable to send to ", target_user_name)
        await send_response(conn, message.stream_id, pdu.MSG_TYPE_MSG_UNSUCCESSFUL, json.dumps({"error": "Target user connection not available"}), version)

async def send_unsuccessful_message_to_sender(conn, message, target_user_id):
    print("Target user not available")
    await send_response(conn, message.stream_id, pdu.MSG_TYPE_MSG_UNSUCCESSFUL, json.dumps({"error": "Target user not available"}))

# User Authentication Functions
async def is_user_authenticated(username, password):
    return user_db.authenticate(username, password)

async def is_user_already_logged_in(username):
    active_users = user_db.get_active_users()
    return username in [user['username'] for user in active_users]

# Broadcast Functions
async def broadcast_active_users(user_login: bool, version=1):

    active_users = user_db.get_active_users()

    if user_login:
        active_users_message = pdu.Datagram(pdu.MSG_TYPE_LOGIN_BROADCAST, json.dumps(active_users), version)
    else:
        active_users_message = pdu.Datagram(pdu.MSG_TYPE_LOGOUT_BROADCAST, json.dumps(active_users), version)
    for (conn, stream_id) in active_user_connections.values():

        await conn.send(QuicStreamEvent(stream_id, active_users_message.to_bytes(), False))


# End of chat_server.py