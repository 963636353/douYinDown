import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlmodel import Session, select
from sqlalchemy import case, func

from .config import PLANS
from .models import Device, Order
from .wechat import PaymentFailure


def utcnow():
    # SQLite 存 UTC 无时区时间；对外统一输出带时区的 ISO 8601。
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat()


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class PaymentService:
    def __init__(self, database, settings, gateway):
        self.db, self.settings, self.gateway = database, settings, gateway

    def register(self, device_id, token=None):
        now, token = utcnow(), token or secrets.token_urlsafe(32)
        with self.db.write() as session:
            existing = session.exec(select(Device).where(Device.device_id == device_id)).first()
            if existing:
                if not secrets.compare_digest(existing.token_hash, token_hash(token)):
                    raise HTTPException(409, "设备已登记，需要原凭据；凭据丢失时请联系客服恢复")
                return {"deviceId": device_id, "deviceToken": token}
            # UUID 关联已提交但响应丢失时，原令牌仍能找回已关联的身份。
            canonical = session.exec(select(Device).where(Device.token_hash == token_hash(token))).first()
            if canonical:
                return {"deviceId": canonical.device_id, "deviceToken": token}
            session.add(Device(device_id=device_id, token_hash=token_hash(token),
                               first_seen=now, last_seen=now,
                               expires_at=now + timedelta(days=self.settings.trial_days)))
        return {"deviceId": device_id, "deviceToken": token}

    def startup(self, owner, computer_uuid):
        now = utcnow()
        with self.db.write() as session:
            device = session.exec(select(Device).where(Device.device_id == owner)).one()
            if owner != computer_uuid:
                try:
                    bound = uuid.UUID(owner).version == 5
                except ValueError:
                    bound = False
                if bound:
                    raise HTTPException(409, "电脑身份不匹配，请联系客服恢复授权")
                collision = session.exec(select(Device).where(Device.device_id == computer_uuid)).first()
                if collision:
                    raise HTTPException(409, "电脑身份已登记，请使用原凭据或联系客服恢复授权")
                orders = session.exec(select(Order).where(Order.device_id == owner)).all()
                for order in orders:
                    order.device_id = computer_uuid
                    session.add(order)
                device.device_id = computer_uuid
            device.last_opened_at = now
            session.add(device)
            session.flush()
            return {"id": device.id, "uuid": device.device_id,
                    "first_opened_at": iso(device.first_seen), "last_opened_at": iso(now),
                    "downloaded_video_count": device.downloaded_video_count,
                    "expires_at": iso(device.expires_at)}

    def record_downloads(self, owner, computer_uuid, count):
        if owner != computer_uuid:
            raise HTTPException(403, "只能上报本电脑的下载数量")
        with self.db.write() as session:
            device = session.exec(select(Device).where(Device.device_id == owner)).one()
            if device.downloaded_video_count > 9223372036854775807 - count:
                raise HTTPException(409, "累计数量超出范围")
            device.downloaded_video_count += count
            session.add(device)
            return {"uuid": owner, "downloaded_video_count": device.downloaded_video_count}

    def stats(self):
        now = utcnow()
        beijing = timezone(timedelta(hours=8))
        midnight = now.replace(tzinfo=timezone.utc).astimezone(beijing).replace(hour=0, minute=0, second=0, microsecond=0)
        thresholds = [(midnight - timedelta(days=days)).astimezone(timezone.utc).replace(tzinfo=None)
                      for days in (0, 6, 29)]
        expressions = [func.coalesce(func.sum(case(
            ((Device.last_opened_at >= start) & (Device.last_opened_at <= now), 1), else_=0)), 0)
            for start in thresholds]
        with Session(self.db.engine) as session:
            values = session.exec(select(func.count(Device.id), *expressions)).one()
        return {"total_users": int(values[0]), "today_active": int(values[1]),
                "active_7d": int(values[2]), "active_30d": int(values[3]),
                "as_of": iso(now), "timezone": "Asia/Shanghai"}

    def authenticate(self, token):
        if not token or not 32 <= len(token) <= 128:
            raise HTTPException(401, "缺少有效的设备令牌")
        with Session(self.db.engine) as session:
            device = session.exec(select(Device).where(Device.token_hash == token_hash(token))).first()
            if device is None:
                raise HTTPException(401, "设备令牌无效")
            return device.device_id

    def license(self, device_id):
        # 回调丢失/购买窗口已关闭时，刷新授权可主动补查待支付订单。
        with Session(self.db.engine) as session:
            pending = session.exec(select(Order).where(Order.device_id == device_id,
                                                       Order.status == "PENDING")
                                   .order_by(Order.created_at.desc()).limit(5)).all()
        if pending:
            # 一次最多补查一笔，避免多个在途订单使授权请求超过客户端超时。
            # 从最近五笔中优先查从未查过/最久未查的订单。
            oldest_checked = min(pending, key=lambda order: order.last_checked_at or datetime.min)
            self.reconcile(oldest_checked.order_id)
        with self.db.write() as session:
            device = session.exec(select(Device).where(Device.device_id == device_id)).one()
            now = utcnow()
            device.last_seen = now
            session.add(device)
            valid = not device.blocked and device.expires_at > now
            status = "disabled" if device.blocked else (
                "active" if valid and device.paid_at else "trial" if valid else "expired")
            return {"valid": valid, "expireTime": iso(device.expires_at),
                    "status": status, "isPaid": device.paid_at is not None,
                    "demo": self.settings.mode == "demo"}

    def create_order(self, device_id, plan_id, client_ip):
        plan = PLANS.get(plan_id)
        if plan is None:
            raise HTTPException(400, "套餐不存在")
        now = utcnow()
        with self.db.write() as session:
            device = session.exec(select(Device).where(Device.device_id == device_id)).one()
            if device.blocked:
                raise HTTPException(403, "设备已禁用，请联系客服")
            order = session.exec(select(Order).where(
                Order.device_id == device_id, Order.plan_id == plan_id,
                Order.status == "PENDING", Order.expires_at > now,
            ).order_by(Order.created_at.desc())).first()
            if order is None:
                order = Order(order_id=uuid.uuid4().hex, device_id=device_id, plan_id=plan_id,
                              amount_cents=plan.amount_cents, grant_days=plan.days,
                              created_at=now, expires_at=now + timedelta(minutes=self.settings.order_minutes))
                session.add(order)
            session.flush()
            session.expunge(order)
        # 同套餐未过期订单复用原订单号；超时后重试也不另建可付款订单。
        if not order.code_url:
            code_url = self.gateway.create(order, f"{self.settings.product_name} {plan.name}", client_ip)
            with self.db.write() as session:
                saved = session.get(Order, order.order_id)
                saved.code_url = code_url
                session.add(saved)
            order.code_url = code_url
        return {"success": True, "orderId": order.order_id,
                "qrcode": self.gateway.image(order.code_url), "expireTime": iso(order.expires_at),
                "demo": self.settings.mode == "demo"}

    def settle(self, data):
        if data.get("return_code") != "SUCCESS" or data.get("result_code") != "SUCCESS":
            raise PaymentFailure("支付结果未成功")
        if data.get("trade_state") not in {None, "SUCCESS"}:
            raise PaymentFailure("订单尚未支付成功")
        transaction_id = data.get("transaction_id", "")
        if not transaction_id or len(transaction_id) > 100:
            raise PaymentFailure("缺少有效的微信交易号")
        try:
            amount = int(data.get("total_fee", ""))
        except (ValueError, TypeError) as exc:
            raise PaymentFailure("支付金额格式错误") from exc
        if data.get("fee_type", "CNY") != "CNY":
            raise PaymentFailure("支付币种不匹配")
        with self.db.write() as session:
            order = session.get(Order, data.get("out_trade_no", ""))
            if order is None or order.amount_cents != amount:
                raise PaymentFailure("订单不存在或支付金额不匹配")
            if order.status == "SUCCESS":
                if order.transaction_id != transaction_id:
                    raise PaymentFailure("订单交易号不匹配")
                return  # 已校验的重复回调直接成功，不重复延长。
            reused = session.exec(select(Order).where(Order.transaction_id == transaction_id)).first()
            if reused is not None:
                raise PaymentFailure("微信交易号已被其他订单使用")
            device = session.exec(select(Device).where(Device.device_id == order.device_id)).one()
            now = utcnow()
            expiry = max(device.expires_at, now) + timedelta(days=order.grant_days)
            device.expires_at, device.paid_at = expiry, now
            order.status, order.transaction_id = "SUCCESS", transaction_id
            order.paid_at, order.granted_until = now, expiry
            session.add(device)
            session.add(order)

    def reconcile(self, order_id, force=False):
        with self.db.write() as session:
            order = session.get(Order, order_id)
            if order is None or order.status != "PENDING":
                return
            now = utcnow()
            interval = 1 if self.settings.mode == "demo" else self.settings.query_interval_seconds
            if not force and order.last_checked_at and (now - order.last_checked_at).total_seconds() < interval:
                return
            order.last_checked_at = now
            session.add(order)
            session.flush()
            session.expunge(order)
        try:
            data = self.gateway.query(order)
            if data.get("out_trade_no") != order_id:
                raise PaymentFailure("查单返回的订单号不匹配")
            if data.get("trade_state") == "SUCCESS":
                self.settle(data)
            elif data.get("trade_state") in {"CLOSED", "REVOKED", "PAYERROR"}:
                with self.db.write() as session:
                    saved = session.get(Order, order_id)
                    # 查询在途时回调可能已开通授权，不允许覆盖成功状态。
                    if saved.status == "PENDING":
                        saved.status = "FAILED" if data["trade_state"] == "PAYERROR" else "CLOSED"
                        session.add(saved)
        except PaymentFailure:
            # 无法确认付款结果时保留待核实状态，不误报“未付款/已关闭”。
            return

    def order_status(self, order_id, device_id):
        with Session(self.db.engine) as session:
            order = session.get(Order, order_id)
            if order is None or order.device_id != device_id:
                raise HTTPException(404, "订单不存在")
        self.reconcile(order_id)
        with Session(self.db.engine) as session:
            return {"status": session.get(Order, order_id).status}
