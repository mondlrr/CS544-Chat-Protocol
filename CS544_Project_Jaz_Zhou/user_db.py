import bcrypt
import threading

class UserDatabase:
    def __init__(self):
        # Example user database
        self.users = {
            "alice": bcrypt.hashpw("p1".encode(), bcrypt.gensalt()),
            "bob": bcrypt.hashpw("p2".encode(), bcrypt.gensalt()),
            "cam": bcrypt.hashpw("p3".encode(), bcrypt.gensalt())
        }
        self.active_users = {}  # Maps user ID to username
        self.user_id_counter = 0
        self.lock = threading.Lock()

    def authenticate(self, username, password):
        if username in self.users:
            # Check the hashed password
            if bcrypt.checkpw(password.encode(), self.users[username]):
                return True
        return False

    def add_user(self, username, password):
        # This function can be used to add new users with a hashed password
        with self.lock:
            if username not in self.users:
                self.users[username] = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
                return True
        return False

    def generate_unique_user_id(self):
        with self.lock:
            self.user_id_counter += 1
            return self.user_id_counter

    def add_active_user(self, user_id, username):
        self.active_users[user_id] = username

    def remove_active_user(self, user_id):
        if user_id in self.active_users:
            del self.active_users[user_id]

    def get_active_users(self):
        return [{"user_id": user_id, "username": username} for user_id, username in self.active_users.items()]

    def get_username(self, user_id):
        return self.active_users[user_id]

user_db = UserDatabase()
