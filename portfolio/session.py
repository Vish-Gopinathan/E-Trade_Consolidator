import os
import pyetrade
from dotenv import load_dotenv

from portfolio import paths

load_dotenv(paths.ROOT / '.env')


def _get_secret(key: str) -> str:
    """Try st.secrets first (Streamlit Cloud), fall back to env var (local dev)."""
    try:
        import streamlit as st
        return st.secrets[key]
    except (KeyError, AttributeError, FileNotFoundError):
        return os.getenv(key, '')


def get_oauth_url():
    """
    Step 1 of the Streamlit OAuth flow.
    Returns (authorization_url, oauth_object, consumer_key, consumer_secret).
    Store all four in st.session_state — complete_oauth() needs the oauth object.
    """
    consumer_key = _get_secret('CONSUMER_KEY')
    consumer_secret = _get_secret('CONSUMER_SECRET')
    oauth = pyetrade.ETradeOAuth(consumer_key, consumer_secret)
    url = oauth.get_request_token()
    return url, oauth, consumer_key, consumer_secret


def complete_oauth(oauth, verifier_code, consumer_key, consumer_secret):
    """
    Step 2 of the Streamlit OAuth flow.
    Exchanges the verifier code for access tokens.
    Returns auth_tokens dict ready for fetch_active_accounts().
    """
    tokens = oauth.get_access_token(verifier_code)
    return {
        'consumer_key': consumer_key,
        'consumer_secret': consumer_secret,
        'oauth_token': tokens['oauth_token'],
        'oauth_token_secret': tokens['oauth_token_secret'],
    }
