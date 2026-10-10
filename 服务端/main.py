"""青柠视频下载独立支付服务：python 服务端/main.py。"""

from payment_server.app import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=3006)
