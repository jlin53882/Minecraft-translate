"""ChatGPT account management controls for the configuration view."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import flet as ft

from app.services_impl.chatgpt_oauth_service import (
    ChatGPTOAuthError,
    begin_chatgpt_login,
    chatgpt_account_status,
    disconnect_chatgpt_account,
    list_chatgpt_models,
    set_active_chatgpt_account,
)
from app.ui.design import C
from app.ui.snack import show_snack
from translation_tool.utils.redaction import redact_text


class ChatGPTOAuthPanel:
    """Own the account selector, OAuth actions, and plan-usage consent state."""

    def __init__(
        self,
        page: ft.Page,
        model_control: ft.Dropdown,
        on_models_loaded: Callable[[list], None],
    ) -> None:
        self.page = page
        self.model_control = model_control
        self.on_models_loaded = on_models_loaded
        self._pending_login = None
        self._busy = False
        self.status_text = ft.Text("尚未連結 ChatGPT 帳號", size=12)
        self.account_selector = ft.Dropdown(
            label="ChatGPT 帳號",
            options=[],
            on_select=self._on_account_selected,
            dense=True,
            width=320,
        )
        self.login_button = ft.OutlinedButton(
            "Continue with ChatGPT", on_click=self._on_login
        )
        self.add_account_button = ft.TextButton(
            "新增帳號", on_click=self._on_add_account
        )
        self.authorize_usage_button = ft.TextButton(
            "啟用 ChatGPT 方案使用權限",
            visible=False,
            on_click=self._on_authorize_usage,
        )
        self.cancel_button = ft.TextButton(
            "取消登入", visible=False, on_click=self._on_cancel
        )
        self.refresh_button = ft.TextButton("更新模型", on_click=self._on_refresh)
        self.disconnect_button = ft.TextButton("中斷連結", on_click=self._on_disconnect)
        self.manage_usage_button = ft.TextButton(
            "管理 ChatGPT 使用量", on_click=self._on_manage_usage
        )
        self.section = ft.Container(
            visible=False,
            content=ft.Column(
                [
                    ft.Text(
                        "使用 ChatGPT 方案或 credits 處理翻譯。翻譯內容會傳送至 OpenAI Responses API.",
                        size=12,
                        color=C.MUTED,
                    ),
                    self.status_text,
                    self.account_selector,
                    ft.Row(
                        [
                            self.login_button,
                            self.add_account_button,
                            self.authorize_usage_button,
                            self.cancel_button,
                            self.refresh_button,
                            self.disconnect_button,
                        ],
                        wrap=True,
                    ),
                    self.manage_usage_button,
                ],
                spacing=6,
            ),
        )

    def refresh_controls(self) -> None:
        try:
            account = chatgpt_account_status()
        except ChatGPTOAuthError as exc:
            account = {"connected": False, "email": ""}
            self.status_text.value = str(exc)
        else:
            if account.get("connected"):
                email = account.get("email")
                identity = f"：{email}" if email else ""
                self.status_text.value = f"已連結 ChatGPT 帳號{identity}。翻譯會使用 ChatGPT 方案或 credits。"
            elif account.get("reauth_required"):
                self.status_text.value = (
                    "ChatGPT 方案授權需要更新；請重新同意後再翻譯。"
                )
            elif account.get("identity_connected") and not account.get(
                "plan_usage_authorized"
            ):
                email = account.get("email")
                identity = f"（{email}）" if email else ""
                self.status_text.value = (
                    f"ChatGPT 身分{identity}已連結，但尚未授權使用方案或 credits。"
                    "按「啟用 ChatGPT 方案使用權限」重新同意。"
                )
            elif account.get("client_id_registered"):
                self.status_text.value = (
                    "ChatGPT 帳號已中斷連結；重新登入會沿用已註冊帳號。"
                )
            else:
                self.status_text.value = "尚未連結 ChatGPT 帳號"

        connected = bool(account.get("connected"))
        account_rows = account.get("accounts") or []
        self.account_selector.options = [
            ft.dropdown.Option(
                key=str(item.get("profile_id") or ""),
                text=(
                    f"ChatGPT 帳號 {index + 1} · {item['email']}"
                    if item.get("email")
                    else f"ChatGPT 帳號 {index + 1}"
                ),
            )
            for index, item in enumerate(account_rows)
            if item.get("profile_id")
        ]
        active_profile_id = str(account.get("active_profile_id") or "")
        if active_profile_id:
            self.account_selector.value = active_profile_id
        self.account_selector.visible = bool(self.account_selector.options)
        self.account_selector.disabled = (
            self._busy or len(self.account_selector.options) < 2
        )
        self.login_button.disabled = self._busy
        self.add_account_button.disabled = self._busy
        self.authorize_usage_button.visible = bool(
            account.get("client_id_registered")
            and (
                not account.get("plan_usage_authorized")
                or account.get("reauth_required")
            )
        )
        self.authorize_usage_button.disabled = self._busy
        self.cancel_button.visible = self._busy
        self.cancel_button.disabled = not self._busy
        self.refresh_button.disabled = self._busy or not connected
        self.disconnect_button.disabled = self._busy or not bool(
            account.get("oauth_session_present") or connected
        )
        has_model = any(option.key for option in self.model_control.options)
        self.model_control.disabled = self._busy or not connected or not has_model

    async def _on_login(self, _event=None) -> None:
        await self._start_login()

    async def _on_add_account(self, _event=None) -> None:
        await self._start_login(add_account=True)

    async def _on_authorize_usage(self, _event=None) -> None:
        await self._start_login(request_plan_usage=True)

    async def _start_login(
        self, *, add_account: bool = False, request_plan_usage: bool = False
    ) -> None:
        if self._busy:
            return
        self._busy = True
        pending = None
        self.status_text.value = "正在準備 ChatGPT 登入…"
        self.refresh_controls()
        self.page.update()
        try:
            pending = await asyncio.to_thread(
                begin_chatgpt_login,
                add_account=add_account,
                request_plan_usage=request_plan_usage,
            )
            self._pending_login = pending
            self.status_text.value = "等待瀏覽器完成 ChatGPT 登入…"
            self.page.update()
            opened = await ft.UrlLauncher().launch_url(
                pending.authorization_url,
                mode=ft.LaunchMode.EXTERNAL_APPLICATION,
            )
            if opened is False:
                raise ChatGPTOAuthError("無法開啟系統瀏覽器，請確認預設瀏覽器設定。")
            result = await asyncio.to_thread(pending.complete)
            self.refresh_controls()
            if result.get("connected"):
                await self._load_models()
                show_snack(self.page, "ChatGPT 已連結；選擇模型後儲存設定。", C.EM)
            else:
                self.status_text.value = (
                    "ChatGPT 身分已連結，但此帳號尚未授權使用方案或 credits。"
                    "可按「啟用 ChatGPT 方案使用權限」重新同意。"
                )
                show_snack(self.page, self.status_text.value)
        except Exception as exc:  # noqa: BLE001 - interactive OAuth boundary
            self.status_text.value = redact_text(exc)
            show_snack(self.page, redact_text(exc))
        finally:
            if pending is not None:
                await asyncio.to_thread(pending.cancel)
            self._pending_login = None
            self._busy = False
            self.refresh_controls()
            self.page.update()

    async def _on_account_selected(self, _event=None) -> None:
        if self._busy:
            return
        profile_id = str(self.account_selector.value or "")
        if not profile_id:
            return
        self._busy = True
        self.refresh_controls()
        try:
            account = await asyncio.to_thread(set_active_chatgpt_account, profile_id)
            self.refresh_controls()
            if account.get("connected"):
                await self._load_models()
            else:
                self.on_models_loaded([])
        except Exception as exc:  # noqa: BLE001 - account switching UI boundary
            self.status_text.value = redact_text(exc)
            show_snack(self.page, redact_text(exc))
        finally:
            self._busy = False
            self.refresh_controls()
            self.page.update()

    async def _on_cancel(self, _event=None) -> None:
        pending = self._pending_login
        if pending is not None:
            await asyncio.to_thread(pending.cancel)

    async def _load_models(self) -> None:
        self.status_text.value = "正在讀取此帳號可用的 ChatGPT 模型…"
        self.page.update()
        try:
            models = await asyncio.to_thread(list_chatgpt_models)
            self.on_models_loaded(models)
            if not models:
                self.status_text.value = "此帳號目前沒有可用的模型。"
            else:
                self.status_text.value = (
                    f"已讀取 {len(models)} 個模型。翻譯會使用 ChatGPT 方案或 credits。"
                )
        except Exception as exc:  # noqa: BLE001 - provider request UI boundary
            self.status_text.value = redact_text(exc)
            show_snack(self.page, redact_text(exc))
        self.refresh_controls()
        self.page.update()

    async def _on_refresh(self, _event=None) -> None:
        if self._busy:
            return
        self._busy = True
        self.refresh_controls()
        try:
            await self._load_models()
        finally:
            self._busy = False
            self.refresh_controls()
            self.page.update()

    async def _on_disconnect(self, _event=None) -> None:
        if self._busy:
            return
        self._busy = True
        self.refresh_controls()
        try:
            revoked = await asyncio.to_thread(disconnect_chatgpt_account)
            self.on_models_loaded([])
            self.status_text.value = (
                "已在此裝置中斷連結 ChatGPT。"
                if revoked
                else "本機登入資料已清除；OpenAI 撤銷狀態未確認。"
            )
            show_snack(self.page, self.status_text.value)
        except Exception as exc:  # noqa: BLE001 - credential revocation UI boundary
            self.status_text.value = redact_text(exc)
            show_snack(self.page, redact_text(exc))
        finally:
            self._busy = False
            self.refresh_controls()
            self.page.update()

    async def _on_manage_usage(self, _event=None) -> None:
        await ft.UrlLauncher().launch_url(
            "https://chatgpt.com/settings/usage",
            mode=ft.LaunchMode.EXTERNAL_APPLICATION,
        )
