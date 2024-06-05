import json

MSG_TYPE_VERSIONS = 0x00

MSG_TYPE_LOGIN = 0x10
MSG_TYPE_LOGIN_ACK = 0x11
MSG_TYPE_LOGIN_BROADCAST = 0x12
MSG_TYPE_LOGIN_UNSUCCESSFUL_RETRY = 0x13
MSG_TYPE_LOGIN_UNSUCCESSFUL_DISCONNECT = 0x14

MSG_TYPE_ALIVE = 0x20

MSG_TYPE_ONE_TO_ONE = 0x30
MSG_TYPE_ONE_TO_MANY = 0x31
MSG_TYPE_BROADCAST = 0x32
MSG_TYPE_MSG_UNSUCCESSFUL = 0x33

MSG_TYPE_LOGOUT = 0x40
MSG_TYPE_LOGOUT_ACK = 0x41
MSG_TYPE_LOGOUT_BROADCAST = 0x42



class Datagram:
    def __init__(self, mtype: int, msg: str, version: int = 1, sz: int = 0):
        self.version = version
        self.mtype = mtype
        self.msg = msg
        self.sz = len(self.msg)


    def to_json(self):
        return json.dumps(self.__dict__)

    @staticmethod
    def from_json(json_str):
        data = json.loads(json_str)
        return Datagram(data['mtype'], data['msg'], data['version'], len(data['msg']))

    def to_bytes(self):
        return self.to_json().encode('utf-8')

    @staticmethod
    def from_bytes(json_bytes):
        return Datagram.from_json(json_bytes.decode('utf-8'))