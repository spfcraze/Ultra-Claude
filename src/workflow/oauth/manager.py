from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from .storage import OAuthToken, OAuthTokenStorage, OAuthClientConfig, oauth_storage


class AuthStatus(Enum):
    NOT_CONFIGURED = "not_configured"
    CONNECTED = "connected"
    EXPIRED = "expired"
    REFRESH_FAILED = "refresh_failed"


@dataclass
class OAuthProviderStatus:
    provider: str
    status: AuthStatus
    account_email: str | None = None
    expires_at: datetime | None = None
    scopes: list[str] | None = None
    has_client_config: bool = False
    
    def to_dict(self) -> dict[str, str | list[str] | bool | None]:
        return {
            "provider": self.provider,
            "status": self.status.value,
            "account_email": self.account_email,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "scopes": self.scopes,
            "has_client_config": self.has_client_config,
        }


class OAuthManager:
    SUPPORTED_PROVIDERS = ["google", "antigravity"]
    
    def __init__(self, storage: OAuthTokenStorage | None = None):
        self._storage = storage or oauth_storage
        self._refresh_callbacks: dict[str, Callable[[OAuthToken], Awaitable[OAuthToken | None]]] = {}
        self._register_default_callbacks()
    
    def _register_default_callbacks(self):
        """Register refresh callbacks for all known providers."""
        try:
            from .flows.google import refresh_google_token
            self._refresh_callbacks["google"] = refresh_google_token
        except ImportError:
            pass
        try:
            from .flows.antigravity import refresh_antigravity_token
            self._refresh_callbacks["antigravity"] = refresh_antigravity_token
        except ImportError:
            pass

    def register_refresh_callback(
        self, 
        provider: str, 
        callback: Callable[[OAuthToken], Awaitable[OAuthToken | None]]
    ) -> None:
        self._refresh_callbacks[provider] = callback
    
    def is_authenticated(self, provider: str, user_id: str = "default") -> bool:
        token = self._storage.load_token(provider, user_id)
        if not token:
            return False
        if token.is_expired():
            return False
        return True
    
    def get_status(self, provider: str, user_id: str = "default") -> OAuthProviderStatus:
        client_config = self._storage.load_client_config(provider)
        token = self._storage.load_token(provider, user_id)
        
        if not token:
            return OAuthProviderStatus(
                provider=provider,
                status=AuthStatus.NOT_CONFIGURED,
                has_client_config=client_config is not None,
            )
        
        if token.is_expired():
            return OAuthProviderStatus(
                provider=provider,
                status=AuthStatus.EXPIRED,
                account_email=token.account_email,
                expires_at=token.expires_at,
                scopes=token.scopes,
                has_client_config=client_config is not None,
            )
        
        return OAuthProviderStatus(
            provider=provider,
            status=AuthStatus.CONNECTED,
            account_email=token.account_email,
            expires_at=token.expires_at,
            scopes=token.scopes,
            has_client_config=client_config is not None,
        )
    
    def get_all_statuses(self, user_id: str = "default") -> dict[str, OAuthProviderStatus]:
        return {
            provider: self.get_status(provider, user_id)
            for provider in self.SUPPORTED_PROVIDERS
        }
    
    def get_access_token(self, provider: str, user_id: str = "default") -> str | None:
        token = self._storage.load_token(provider, user_id)
        if not token:
            return None
        if token.is_expired():
            return None
        return token.access_token
    
    async def get_valid_access_token(self, provider: str, user_id: str = "default") -> str | None:
        token = self._storage.load_token(provider, user_id)
        if not token:
            return None
        
        if token.expires_soon(minutes=5):
            refreshed = await self._try_refresh(token)
            if refreshed:
                return refreshed.access_token
            elif token.is_expired():
                return None
        
        return token.access_token
    
    async def _try_refresh(self, token: OAuthToken) -> OAuthToken | None:
        if not token.refresh_token:
            return None
        
        callback = self._refresh_callbacks.get(token.provider)
        if not callback:
            return None
        
        try:
            refreshed_token = await callback(token)
            if refreshed_token:
                self._storage.save_token(refreshed_token)
                return refreshed_token
        except Exception:
            pass
        
        return None
    
    def save_token(self, token: OAuthToken) -> int:
        return self._storage.save_token(token)
    
    def revoke(self, provider: str, user_id: str = "default") -> bool:
        return self._storage.delete_token(provider, user_id)
    
    def has_client_config(self, provider: str) -> bool:
        return self._storage.load_client_config(provider) is not None
    
    def get_client_config(self, provider: str) -> OAuthClientConfig | None:
        return self._storage.load_client_config(provider)
    
    def save_client_config(self, config: OAuthClientConfig) -> int:
        return self._storage.save_client_config(config)
    
    def delete_client_config(self, provider: str) -> bool:
        return self._storage.delete_client_config(provider)


oauth_manager = OAuthManager()
