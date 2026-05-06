"""AES-256-GCM encryption helpers for the X-DB-URL header.

Token format (base64): nonce[12] | ciphertext | tag[16]

Compatible JS — Browser (Web Crypto API, works in Next.js client components too):

    async function encryptDbUrl(url: string, secret: string): Promise<string> {
        const enc = new TextEncoder();
        const keyBytes = await crypto.subtle.digest('SHA-256', enc.encode(secret));
        const key = await crypto.subtle.importKey(
            'raw', keyBytes, { name: 'AES-GCM' }, false, ['encrypt']
        );
        const nonce = crypto.getRandomValues(new Uint8Array(12));
        const ciphertext = await crypto.subtle.encrypt(
            { name: 'AES-GCM', iv: nonce }, key, enc.encode(url)
        );
        // Web Crypto appends the 16-byte tag to ciphertext automatically
        const out = new Uint8Array(12 + ciphertext.byteLength);
        out.set(nonce, 0);
        out.set(new Uint8Array(ciphertext), 12);
        return btoa(String.fromCharCode(...out));
    }
"""
import base64
import hashlib

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _derive_key(secret: str) -> bytes:
    return hashlib.sha256(secret.encode()).digest()


def decrypt_db_url(token: str, secret: str) -> str:
    """Decrypt a base64-encoded AES-256-GCM token into a plain DB URL string."""
    key = _derive_key(secret)
    data = base64.b64decode(token)
    if len(data) < 12 + 16:
        raise ValueError("Token too short")
    nonce, ct_and_tag = data[:12], data[12:]
    return AESGCM(key).decrypt(nonce, ct_and_tag, None).decode()
