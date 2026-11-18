import hashlib
from Crypto.Cipher import AES


def password_digest(password):
    return hashlib.md5(password.encode()).hexdigest()


def strong_digest(password):
    return hashlib.sha256(password.encode()).hexdigest()


def encrypt_block(key, block):
    cipher = AES.new(key, AES.MODE_ECB)
    return cipher.encrypt(block)
