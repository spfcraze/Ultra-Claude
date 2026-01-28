from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import base64
import json
import os

from ...database import db


def _simple_encrypt(data: str) -> str:
    if not data:
        return ""
    key = os.environ.get("AUTOWRKERS_ENCRYPTION_KEY", "default-dev-key-change-in-prod")
    key_bytes = key.encode()[:32].ljust(32, b'\0')
    data_bytes = data.encode()
    encrypted = bytes(a ^ b for a, b in zip(data_bytes, key_bytes * (len(data_bytes) // 32 + 1)))
    return base64.b64encode(encrypted).decode()


def _simple_decrypt(encrypted: str) -> str:
    if not encrypted:
        return ""
    key = os.environ.get("AUTOWRKERS_ENCRYPTION_KEY", "default-dev-key-change-in-prod")
    key_bytes = key.encode()[:32].ljust(32, b'\0')
    encrypted_bytes = base64.b64decode(encrypted)
    decrypted = bytes(a ^ b for a, b in zip(encrypted_bytes, key_bytes * (len(encrypted_bytes) // 32 + 1)))
    return decrypted.decode()


@dataclass
class OAuthToken:
    provider: str
    access_token: str
    refresh_token: Optional[str] = None
    token_uri: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    scopes: list[str] | None = None
    expires_at: Optional[datetime] = None
    account_email: Optional[str] = None
    user_id: str = "default"
    
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        return datetime.now() >= self.expires_at
    
    def expires_soon(self, minutes: int = 5) -> bool:
        if not self.expires_at:
            return False
        from datetime import timedelta
        return datetime.now() >= (self.expires_at - timedelta(minutes=minutes))


@dataclass
class OAuthClientConfig:
    provider: str
    client_config: dict
    
    @classmethod
    def from_google_json(cls, json_content: str | dict) -> "OAuthClientConfig":
        if isinstance(json_content, str):
            config = json.loads(json_content)
        else:
            config = json_content
        return cls(provider="google", client_config=config)


class OAuthTokenStorage:
    
    def save_token(self, token: OAuthToken) -> int:
        expires_at_str = token.expires_at.isoformat() if token.expires_at else None
        
        return db.save_oauth_token({
            'provider': token.provider,
            'user_id': token.user_id,
            'access_token_encrypted': _simple_encrypt(token.access_token),
            'refresh_token_encrypted': _simple_encrypt(token.refresh_token or ''),
            'token_uri': token.token_uri or '',
            'client_id': token.client_id or '',
            'client_secret_encrypted': _simple_encrypt(token.client_secret or ''),
            'scopes': token.scopes or [],
            'expires_at': expires_at_str,
            'account_email': token.account_email or '',
        })
    
    def load_token(self, provider: str, user_id: str = 'default') -> Optional[OAuthToken]:
        data = db.get_oauth_token(provider, user_id)
        if not data:
            return None
        
        expires_at = None
        if data.get('expires_at'):
            try:
                expires_at = datetime.fromisoformat(data['expires_at'])
            except ValueError:
                pass
        
        return OAuthToken(
            provider=data['provider'],
            access_token=_simple_decrypt(data['access_token_encrypted']),
            refresh_token=_simple_decrypt(data['refresh_token_encrypted']) or None,
            token_uri=data['token_uri'] or None,
            client_id=data['client_id'] or None,
            client_secret=_simple_decrypt(data['client_secret_encrypted']) or None,
            scopes=data.get('scopes'),
            expires_at=expires_at,
            account_email=data.get('account_email') or None,
            user_id=data['user_id'],
        )
    
    def delete_token(self, provider: str, user_id: str = 'default') -> bool:
        return db.delete_oauth_token(provider, user_id)
    
    def update_access_token(
        self, 
        provider: str, 
        access_token: str, 
        expires_at: Optional[datetime],
        user_id: str = 'default'
    ) -> bool:
        expires_at_str = expires_at.isoformat() if expires_at else None
        return db.update_oauth_token_expiry(
            provider,
            _simple_encrypt(access_token),
            expires_at_str or '',
            user_id,
        )
    
    def list_tokens(self, user_id: str = 'default') -> list[OAuthToken]:
        data_list = db.get_all_oauth_tokens(user_id)
        tokens = []
        for data in data_list:
            expires_at = None
            if data.get('expires_at'):
                try:
                    expires_at = datetime.fromisoformat(data['expires_at'])
                except ValueError:
                    pass
            
            tokens.append(OAuthToken(
                provider=data['provider'],
                access_token=_simple_decrypt(data['access_token_encrypted']),
                refresh_token=_simple_decrypt(data['refresh_token_encrypted']) or None,
                token_uri=data['token_uri'] or None,
                client_id=data['client_id'] or None,
                client_secret=_simple_decrypt(data['client_secret_encrypted']) or None,
                scopes=data.get('scopes'),
                expires_at=expires_at,
                account_email=data.get('account_email') or None,
                user_id=data['user_id'],
            ))
        return tokens

    def save_client_config(self, config: OAuthClientConfig) -> int:
        encrypted = _simple_encrypt(json.dumps(config.client_config))
        return db.save_oauth_client_config(config.provider, encrypted)
    
    def load_client_config(self, provider: str) -> Optional[OAuthClientConfig]:
        data = db.get_oauth_client_config(provider)
        if not data:
            return None
        
        decrypted = _simple_decrypt(data['client_config_encrypted'])
        client_config = json.loads(decrypted)
        return OAuthClientConfig(provider=provider, client_config=client_config)
    
    def delete_client_config(self, provider: str) -> bool:
        return db.delete_oauth_client_config(provider)


oauth_storage = OAuthTokenStorage()
