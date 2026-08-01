"""PSN AccountID helper adapted from Chiaki's original script."""

import base64
import webbrowser
from urllib.parse import parse_qs, quote, urlparse

import requests


CLIENT_ID = "ba495a24-818c-472b-b12d-ff231c1b5745"
CLIENT_SECRET = "mvaiZkRsAsI1IBkY"
REDIRECT_URI = "https://remoteplay.dl.playstation.net/remoteplay/redirect"
LOGIN_URL = (
    "https://auth.api.sonyentertainmentnetwork.com/2.0/oauth/authorize?"
    "service_entity=urn:service-entity:psn&response_type=code&"
    f"client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&scope=psn:clientapp&"
    "request_locale=en_US&ui=pr&service_logo=ps&layout_type=popup&"
    "smcid=remoteplay&prompt=always&PlatformPrivacyWs1=minimal&"
)
TOKEN_URL = "https://auth.api.sonyentertainmentnetwork.com/2.0/oauth/token"


def account_id_from_redirect(redirect_url: str, timeout: float = 30.0) -> str:
    """Exchange a pasted Sony redirect URL for Chiaki's little-endian ID."""
    query = parse_qs(urlparse(redirect_url.strip()).query)
    codes = query.get("code", [])
    if not codes or not codes[0]:
        raise ValueError("redirect URL 中没有 code 参数；请粘贴登录后地址栏的完整 URL")

    auth = requests.auth.HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET)
    response = requests.post(
        TOKEN_URL,
        auth=auth,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=(
            f"grant_type=authorization_code&code={quote(codes[0])}&"
            f"redirect_uri={quote(REDIRECT_URI)}&"
        ).encode("ascii"),
        timeout=timeout,
    )
    if response.status_code != 200:
        raise RuntimeError(f"OAuth token 请求失败（HTTP {response.status_code}）")

    token_json = response.json()
    token = token_json.get("access_token")
    if not token:
        raise RuntimeError("OAuth 响应中缺少 access_token")

    account_response = requests.get(
        f"{TOKEN_URL}/{quote(token)}",
        auth=auth,
        timeout=timeout,
    )
    if account_response.status_code != 200:
        raise RuntimeError(
            f"Account Info 请求失败（HTTP {account_response.status_code}）"
        )

    account_info = account_response.json()
    try:
        user_id = int(account_info["user_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Account Info 中缺少有效 user_id") from exc
    if not 0 <= user_id < 1 << 64:
        raise RuntimeError("user_id 超出 64 位范围")
    return base64.b64encode(user_id.to_bytes(8, "little")).decode("ascii")


def run_account_id_prompt() -> int:
    """Run the interactive AccountID flow without exposing OAuth tokens."""
    print("PSN AccountID 获取")
    print("正在打开 Sony 登录页面，请登录你的 PSN 账号。")
    print("登录后页面显示 redirect 时，复制地址栏中的完整 URL。")
    print()
    print(LOGIN_URL)
    try:
        webbrowser.open(LOGIN_URL)
    except Exception:
        pass
    redirect_url = input("粘贴 redirect URL: ").strip()
    try:
        account_id = account_id_from_redirect(redirect_url)
    except Exception as exc:
        print(f"获取失败：{exc}")
        return 1
    print()
    print("AccountID:")
    print(account_id)
    print("以上是可填入 Chiaki 的 AccountID；OAuth token 未保存。")
    return 0
