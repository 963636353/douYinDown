import ipaddress
import json
import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, UUID5
from starlette.concurrency import run_in_threadpool

from .config import PLANS, SERVER_DIR, Settings
from .database import Database
from .service import PaymentService
from .stats_page import STATS_HTML
from .wechat import DemoGateway, PaymentFailure, WeChatGateway, to_xml


DeviceId = Annotated[str, Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


class Registration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deviceId: DeviceId
    deviceToken: Annotated[str, Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")] | None = None


class NewOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deviceId: DeviceId
    planId: Annotated[str, Field(min_length=1, max_length=64)]


class Startup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    uuid: UUID5


class DownloadCount(Startup):
    # 兼容已安装的上一版统计客户端：旧 report_id 忽略，不再去重。
    model_config = ConfigDict(extra="ignore")
    count: Annotated[StrictInt, Field(gt=0, le=1000000)]


def create_app(settings=None, gateway=None):
    settings = settings or Settings.from_env()
    database = Database(settings.database_url)
    gateway = gateway or (DemoGateway() if settings.mode == "demo" else WeChatGateway(settings))
    service = PaymentService(database, settings, gateway)

    @asynccontextmanager
    async def lifespan(app):
        await run_in_threadpool(database.initialize)
        yield
        database.engine.dispose()

    app = FastAPI(title="青柠视频下载支付服务", lifespan=lifespan)
    app.state.payments = service

    if settings.mode == "demo":
        @app.middleware("http")
        async def local_demo_only(request, call_next):
            host = request.client.host if request.client else ""
            try:
                local = ipaddress.ip_address(host).is_loopback
            except ValueError:
                local = host == "testclient"
            if not local:
                return JSONResponse({"detail": "演示模式仅供本机使用"}, status_code=403)
            return await call_next(request)

    @app.exception_handler(PaymentFailure)
    async def payment_failure(_request, exc):
        return JSONResponse({"success": False, "message": str(exc)}, status_code=502)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _exc):
        # 默认验证错误可能包含原请求值；避免回显设备令牌。
        return JSONResponse({"detail": "请求参数不符合接口要求"}, status_code=422)

    def current_device(authorization: Annotated[str | None, Header()] = None):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "需要设备令牌")
        return service.authenticate(authorization[7:])

    DeviceDep = Annotated[str, Depends(current_device)]

    @app.get("/health")
    def health():
        return {"ok": True, "mode": settings.mode, "product": settings.product_name}

    @app.get("/videoDown.txt")
    def update_info():
        # 固定文件、按请求读取：宝塔保存修改后即可生效，不公开项目目录。
        headers = {"Cache-Control": "no-store"}
        try:
            data = json.loads((SERVER_DIR / "videoDown.txt").read_text(encoding="utf-8-sig"))
            fields = ("version_number", "百度", "夸克", "info", "forced_upgrade")
            if not isinstance(data, dict) or any(key not in data for key in fields):
                raise ValueError("缺少更新字段")
            if any(not isinstance(data[key], str) for key in fields[:-1]) or not data["version_number"].strip():
                raise ValueError("更新字段格式错误")
            if not isinstance(data["forced_upgrade"], (bool, str)):
                raise ValueError("强制升级字段格式错误")
            return JSONResponse({key: data[key] for key in fields}, headers=headers)
        except (OSError, ValueError, TypeError):
            return JSONResponse({"detail": "更新信息文件缺失或格式不正确，请联系管理员"},
                                status_code=503, headers=headers)

    @app.get("/stats", response_class=HTMLResponse, include_in_schema=False)
    def stats_page():
        return HTMLResponse(STATS_HTML, headers={"Cache-Control": "no-store"})

    @app.get("/api/pc/stats")
    def stats():
        return JSONResponse(service.stats(), headers={"Cache-Control": "no-store"})

    @app.post("/api/pc/startup")
    def startup(body: Startup, owner: DeviceDep):
        return service.startup(owner, body.uuid.hex)

    @app.post("/api/pc/download-count")
    def download_count(body: DownloadCount, owner: DeviceDep):
        return service.record_downloads(owner, body.uuid.hex, body.count)

    @app.post("/api/pc/register", status_code=201)
    def register(body: Registration):
        return service.register(body.deviceId, body.deviceToken)

    @app.get("/api/pc/plans")
    def plans():
        return [{"planId": key, "name": value.name,
                 "price": f"{value.amount_cents / 100:.2f}", "days": value.days}
                for key, value in PLANS.items()]

    @app.get("/api/pc/license/{device_id}")
    def license(device_id: str, owner: DeviceDep):
        if device_id != owner:
            raise HTTPException(403, "只能查询本设备的授权")
        return service.license(owner)

    @app.post("/api/pc/create-order")
    def create_order(body: NewOrder, request: Request, owner: DeviceDep):
        if body.deviceId != owner:
            raise HTTPException(403, "只能为本设备创建订单")
        # 使用可信反向代理时由部署层设置真实客户端 IP，不信任任意转发头。
        client_ip = request.client.host if request.client else "127.0.0.1"
        return service.create_order(owner, body.planId, client_ip)

    @app.get("/api/pc/order-status/{order_id}")
    def order_status(order_id: str, owner: DeviceDep):
        return service.order_status(order_id, owner)

    @app.post("/notify", include_in_schema=False)
    async def notify(request: Request):
        try:
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 65536:
                    raise PaymentFailure("支付报文过大")
            data = await run_in_threadpool(gateway.notification, bytes(raw))
            await run_in_threadpool(service.settle, data)
            reply = {"return_code": "SUCCESS", "return_msg": "OK"}
        except Exception:
            # 不记录原始报文、签名或密钥。微信稍后可重试未提交的事务。
            logging.getLogger(__name__).warning("支付通知处理失败，等待通知重试或主动查单")
            reply = {"return_code": "FAIL", "return_msg": "REJECTED"}
        return Response(to_xml(reply), media_type="text/xml")

    return app
