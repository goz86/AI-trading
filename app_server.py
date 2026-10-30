"""AI Trading Studio - Apple Design Edition Backend Server
Provides local REST API + Web UI for TradingAgents multi-agent trading system.
Supports Google Gemini, DeepSeek, OpenAI, Anthropic with real-time live updates.
"""
import argparse
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# --- SSL Patch for Windows & non-ASCII paths (yfinance / curl fix) ---
try:
    import certifi
    _public_cert = Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "TradingAgents-cert"
    if all(ord(c) < 128 for c in str(_public_cert)):
        _dst_cert = _public_cert / "cacert.pem"
        _dst_cert.parent.mkdir(parents=True, exist_ok=True)
        if not _dst_cert.exists() or _dst_cert.stat().st_size != Path(certifi.where()).stat().st_size:
            shutil.copyfile(certifi.where(), _dst_cert)
        certifi.where = lambda: str(_dst_cert)
        for k in ("CURL_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
            os.environ[k] = str(_dst_cert)
except Exception as _e:
    print("Warning: SSL cert path patch bypassed:", _e)

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
RESULTS_DIR = ROOT_DIR / "Kết quả"
RESULTS_DIR.mkdir(exist_ok=True)
CONFIG_FILE = ROOT_DIR / "config_user.json"
WEB_DIR = ROOT_DIR / "web"
WEB_DIR.mkdir(exist_ok=True)

# Vietnamese translation for LangGraph agent nodes
STEP_NAMES_VI = {
    "Khởi động": "Khởi động hệ thống",
    "Market Analyst": "Phòng Kỹ thuật (Đọc nến & Chỉ báo)",
    "Social Analyst": "Phòng Tâm lý (Mạng xã hội & Cộng đồng)",
    "Sentiment Analyst": "Phòng Tâm lý (Mạng xã hội & Cộng đồng)",
    "News Analyst": "Phòng Tin tức (Tin vĩ mô & Sự kiện)",
    "Fundamentals Analyst": "Phòng Tài chính (Đọc báo cáo tài chính)",
    "Memory Log": "Sổ nhớ (So sánh & Rút kinh nghiệm lệnh cũ)",
    "Bull Researcher": "Phe Mua (Tìm kiếm luận điểm tăng giá)",
    "Bear Researcher": "Phe Bán (Tìm kiếm rủi ro giảm giá)",
    "Research Manager": "Trưởng phòng Nghiên cứu (Điều phối tranh biện)",
    "Trader": "Nhân viên Giao dịch (Lập kế hoạch vào/ra lệnh)",
    "Aggressive Analyst": "Hội đồng Rủi ro: Góc nhìn Liều lĩnh",
    "Conservative Analyst": "Hội đồng Rủi ro: Góc nhìn Thận trọng",
    "Neutral Analyst": "Hội đồng Rủi ro: Góc nhìn Trung lập",
    "Portfolio Manager": "Giám đốc Danh mục (Ra quyết định cuối cùng)",
}

# In-memory execution state
EXECUTION_STATE = {
    "is_running": False,
    "task_id": None,
    "symbol": "",
    "date": "",
    "provider": "",
    "current_step": "Sẵn sàng",
    "current_step_vi": "Hệ thống sẵn sàng",
    "node_history": [],
    "ai_calls": 0,
    "start_time": 0,
    "elapsed_seconds": 0,
    "progress_pct": 0,
    "error": None,
    "result": None,
}
STATE_LOCK = threading.Lock()


def load_user_config() -> dict:
    cfg = None
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    if not cfg:
        cfg = {
            "provider": "google",
            "keys": {
                "google": "",
                "deepseek": "",
                "openai": "",
                "anthropic": "",
                "custom": ""
            },
            "models": {
                "google_quick": "gemini-3.5-flash-lite",
                "google_deep": "gemini-3.8-flash",
                "deepseek_quick": "deepseek-chat",
                "deepseek_deep": "deepseek-reasoner",
                "openai_quick": "gpt-4o-mini",
                "openai_deep": "gpt-4o",
                "anthropic_quick": "claude-3-5-haiku-latest",
                "anthropic_deep": "claude-3-5-sonnet-latest",
                "custom_quick": "",
                "custom_deep": "",
                "custom_base_url": "https://api.deepseek.com"
            },
            "default_symbol": "BTC-USD",
            "analysts": ["market", "social", "news"]
        }

    # Auto-load existing Gemini key from khoa-gemini.txt if available
    gemini_file = ROOT_DIR / "khoa-gemini.txt"
    if gemini_file.exists():
        try:
            saved_key = gemini_file.read_text(encoding="utf-8-sig").strip()
            if saved_key and not cfg.get("keys", {}).get("google"):
                cfg["keys"]["google"] = saved_key
        except Exception:
            pass

    # Safety migration: Google deprecated Gemini 2.5 line (404 NOT_FOUND). Migrate to Gemini 3.5 & 3.8
    models = cfg.setdefault("models", {})
    if "2.5" in str(models.get("google_quick", "")) or not models.get("google_quick"):
        models["google_quick"] = "gemini-3.5-flash-lite"
    if "2.5" in str(models.get("google_deep", "")) or not models.get("google_deep"):
        models["google_deep"] = "gemini-3.8-flash"

    cfg.setdefault("telegram", {
        "enabled": False,
        "bot_token": "",
        "chat_id": ""
    })

    return cfg


def save_user_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    # Also save gemini key to khoa-gemini.txt for compatibility with .bat
    g_key = cfg.get("keys", {}).get("google", "").strip()
    if g_key and not (" " in g_key or "\\" in g_key):
        (ROOT_DIR / "khoa-gemini.txt").write_text(g_key, encoding="utf-8")


def clean_symbol(symbol: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in symbol.strip().upper())


def test_api_key(provider: str, api_key: str, base_url: str = "") -> dict:
    provider = provider.lower()
    api_key = api_key.strip()
    if not api_key:
        return {"ok": False, "message": "Vui lòng nhập API Key."}

    # Detect if user accidentally pasted a local file path instead of an API Key
    if "\\" in api_key or "/" in api_key or " " in api_key or api_key.startswith("C:") or "Users" in api_key:
        return {
            "ok": False,
            "message": (
                "LỖI: Bạn đang dán nhầm đường dẫn thư mục máy tính vào ô khóa API!\n"
                f"Giá trị vừa nhập: '{api_key[:50]}...'\n\n"
                "👉 Hướng dẫn: Khóa Google Gemini đúng là chuỗi ký tự dài bắt đầu bằng 'AIzaSy...' hoặc 'AQ...'. "
                "Hãy vào https://aistudio.google.com/apikey bấm nút 'Create API key' hoặc 'Copy' để lấy khóa thật."
            )
        }

    try:
        if provider == "google":
            safe_key = urllib.parse.quote(api_key)
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={safe_key}"
            req = urllib.request.Request(url, headers={"User-Agent": "TradingStudio/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name", "").replace("models/", "") for m in data.get("models", [])]
                flash_models = [m for m in models if "flash" in m or "pro" in m][:5]
                return {"ok": True, "message": f"Khóa Google Gemini hợp lệ! Mô hình sẵn có: {', '.join(flash_models)}"}

        elif provider == "deepseek":
            url = "https://api.deepseek.com/models"
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "TradingStudio/1.0"
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("id", "") for m in data.get("data", [])]
                return {"ok": True, "message": f"Khóa DeepSeek hợp lệ! Mô hình: {', '.join(models)}"}

        elif provider == "openai":
            url = "https://api.openai.com/v1/models"
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "TradingStudio/1.0"
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return {"ok": True, "message": "Khóa OpenAI hợp lệ và kết nối thành công!"}

        elif provider == "anthropic":
            url = "https://api.anthropic.com/v1/models"
            req = urllib.request.Request(url, headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "User-Agent": "TradingStudio/1.0"
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                return {"ok": True, "message": "Khóa Anthropic Claude hợp lệ!"}

        elif provider == "custom":
            b_url = (base_url or "https://api.deepseek.com").rstrip("/")
            url = f"{b_url}/models"
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "TradingStudio/1.0"
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                return {"ok": True, "message": f"Kết nối máy chủ tùy chỉnh ({b_url}) thành công!"}

        return {"ok": False, "message": f"Nhà cung cấp '{provider}' chưa hỗ trợ kiểm tra nhanh."}

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        return {"ok": False, "message": f"Lỗi HTTP {e.code}: {e.reason}. Chi tiết: {body[:250]}"}
    except Exception as e:
        return {"ok": False, "message": f"Không thể kết nối: {str(e)}"}


def scan_history() -> list:
    history = []
    if not RESULTS_DIR.exists():
        return history

    for item in RESULTS_DIR.iterdir():
        if item.is_dir():
            symbol = item.name
            jsonl_file = item / "ket_qua.jsonl"
            if jsonl_file.exists():
                try:
                    for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                        if line.strip():
                            data = json.loads(line)
                            sym = clean_symbol(data.get("ma", symbol))
                            dt = data.get("ngay", "")
                            md_name = f"Biên bản {sym} {dt}.md"
                            pdf_name = f"Tóm tắt biên bản {sym} {dt}.pdf"
                            data["has_report"] = (item / md_name).exists()
                            data["has_pdf"] = (item / pdf_name).exists()
                            data["pdf_file"] = pdf_name if (item / pdf_name).exists() else None
                            data["folder"] = str(item)
                            history.append(data)
                except Exception:
                    pass

            # Also check for individual md files if jsonl was not updated
            for md in item.glob("Biên bản *.md"):
                date_part = md.stem.split(" ")[-1] if " " in md.stem else ""
                exists = any(h.get("ma") == symbol and h.get("ngay") == date_part for h in history)
                if not exists:
                    pdf_name = f"Tóm tắt biên bản {clean_symbol(symbol)} {date_part}.pdf"
                    has_pdf = (item / pdf_name).exists()
                    history.append({
                        "ma": symbol,
                        "ngay": date_part,
                        "rating": "Xem biên bản",
                        "phut": 0,
                        "so_luot_ai": 0,
                        "has_report": True,
                        "has_pdf": has_pdf,
                        "pdf_file": pdf_name if has_pdf else None,
                        "folder": str(item)
                    })

    # Sort descending by date
    history.sort(key=lambda x: str(x.get("ngay", "")), reverse=True)
    return history


def load_report_detail(symbol: str, date: str) -> dict:
    sym = clean_symbol(symbol)
    folder = RESULTS_DIR / sym
    json_path = folder / f"report_{sym}_{date}.json"
    md_path = folder / f"Biên bản {sym} {date}.md"
    pdf_path = folder / f"Tóm tắt biên bản {sym} {date}.pdf"
    summary_path = folder / f"summary_vi_{sym}_{date}.json"

    res = {}
    if json_path.exists():
        try:
            res = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    if not res and md_path.exists():
        content = md_path.read_text(encoding="utf-8")
        res = {
            "symbol": symbol,
            "date": date,
            "rating": "COMPLETED",
            "markdown": content,
            "sections": {"complete_report": content}
        }

    if not res:
        return {"error": f"Không tìm thấy báo cáo cho {symbol} ngày {date}"}

    res["has_pdf"] = pdf_path.exists()
    res["pdf_file"] = pdf_path.name if pdf_path.exists() else None
    if summary_path.exists():
        try:
            res["summary_vi"] = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Ensure summary_vi is always present even if loaded from older run
    if "summary_vi" not in res:
        try:
            import pdf_generator
            cfg = load_user_config()
            prov = res.get("provider", "google")
            key = cfg.get("keys", {}).get(prov) or cfg.get("keys", {}).get("google")
            s_data = pdf_generator.generate_summary_data(res, api_key=key, provider=prov)
            if s_data:
                res["summary_vi"] = s_data
                try:
                    summary_path.write_text(json.dumps(s_data, indent=2, ensure_ascii=False), encoding="utf-8")
                except Exception:
                    pass
        except Exception as _e:
            print("Auto-generate summary_vi error:", _e)

    return res


CANDLE_CACHE = {}


def fetch_candles(symbol: str, period: str = "6mo", interval: str = "1d") -> dict:
    sym = clean_symbol(symbol)
    cache_key = f"{sym}_{period}_{interval}"
    now = time.time()
    
    # 5-minute memory cache
    if cache_key in CANDLE_CACHE:
        c_time, c_data = CANDLE_CACHE[cache_key]
        if now - c_time < 300:
            return c_data

    # Map aliases for yfinance
    yf_symbol = sym
    if sym in ("BTC", "BITCOIN"):
        yf_symbol = "BTC-USD"
    elif sym in ("ETH", "ETHEREUM"):
        yf_symbol = "ETH-USD"
    elif sym in ("SOL", "SOLANA"):
        yf_symbol = "SOL-USD"
    elif sym in ("BNB", "BINANCECOIN"):
        yf_symbol = "BNB-USD"
    elif sym in ("XAU", "GOLD", "VANG", "XAU-USD", "XAUUSD"):
        yf_symbol = "GC=F"
    
    try:
        import yfinance as yf
        import pandas as pd
        
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period=period, interval=interval)
        if df.empty and yf_symbol != sym:
            ticker = yf.Ticker(sym)
            df = ticker.history(period=period, interval=interval)
            
        if df.empty:
            return {"ok": False, "error": f"Không có dữ liệu nến cho mã {symbol}"}
            
        candles = []
        volumes = []
        for idx, row in df.iterrows():
            date_str = idx.strftime("%Y-%m-%d")
            c_val = round(float(row["Close"]), 2)
            o_val = round(float(row["Open"]), 2)
            h_val = round(float(row["High"]), 2)
            l_val = round(float(row["Low"]), 2)
            v_val = int(row.get("Volume", 0))
            
            candles.append({
                "time": date_str,
                "open": o_val,
                "high": h_val,
                "low": l_val,
                "close": c_val
            })
            volumes.append({
                "time": date_str,
                "value": v_val,
                "color": "#26a69a" if c_val >= o_val else "#ef5350"
            })
            
        close_s = df["Close"]
        sma200 = close_s.rolling(window=200, min_periods=20).mean()
        sma50 = close_s.rolling(window=50, min_periods=10).mean()
        ema10 = close_s.ewm(span=10, adjust=False).mean()
        
        sma200_line = [{"time": idx.strftime("%Y-%m-%d"), "value": round(float(v), 2)} for idx, v in sma200.items() if not pd.isna(v)]
        sma50_line = [{"time": idx.strftime("%Y-%m-%d"), "value": round(float(v), 2)} for idx, v in sma50.items() if not pd.isna(v)]
        ema10_line = [{"time": idx.strftime("%Y-%m-%d"), "value": round(float(v), 2)} for idx, v in ema10.items() if not pd.isna(v)]
        
        res = {
            "ok": True,
            "symbol": sym,
            "yf_symbol": yf_symbol,
            "candles": candles,
            "volumes": volumes,
            "sma200": sma200_line,
            "sma50": sma50_line,
            "ema10": ema10_line,
            "current_price": candles[-1]["close"] if candles else 0
        }
        CANDLE_CACHE[cache_key] = (now, res)
        return res
    except Exception as e:
        return {"ok": False, "error": f"Lỗi lấy dữ liệu thị trường: {str(e)}"}


def build_multipart_form(fields: dict, files: dict) -> tuple[bytes, str]:
    boundary = "----TelegramFormBoundary" + uuid.uuid4().hex
    body = bytearray()
    for k, v in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(v).encode("utf-8"))
        body.extend(b"\r\n")
    for field_name, (filename, file_bytes, content_type) in files.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode("utf-8"))
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
        body.extend(file_bytes)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    content_type_hdr = f"multipart/form-data; boundary={boundary}"
    return bytes(body), content_type_hdr


def test_telegram_connection(bot_token: str, chat_id: str) -> dict:
    bot_token = bot_token.strip()
    chat_id = chat_id.strip()
    if not bot_token or not chat_id:
        return {"ok": False, "message": "Vui lòng nhập cả Bot Token và Chat ID."}
    
    try:
        # 1. Check Bot validity via getMe
        me_url = f"https://api.telegram.org/bot{bot_token}/getMe"
        req = urllib.request.Request(me_url, headers={"User-Agent": "TradingStudio/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            me_data = json.loads(resp.read().decode("utf-8"))
            if not me_data.get("ok"):
                return {"ok": False, "message": "Bot Token không hợp lệ!"}
            bot_name = me_data.get("result", {}).get("first_name", "Bot")
            username = me_data.get("result", {}).get("username", "")

        # 2. Send test notification
        msg_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        text = (
            f"🔔 <b>AI Trading Studio - Kết Nối Thành Công!</b>\n\n"
            f"✅ Bot <b>@{username}</b> ({bot_name}) đã được liên kết với hệ thống AI Trading đa tác tử.\n"
            f"Báo cáo phân tích và file PDF chuẩn 5 Phút Trading sẽ tự động gửi vào đây ngay khi có kết quả."
        )
        payload = json.dumps({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }).encode("utf-8")
        msg_req = urllib.request.Request(msg_url, data=payload, headers={
            "Content-Type": "application/json",
            "User-Agent": "TradingStudio/1.0"
        })
        with urllib.request.urlopen(msg_req, timeout=12) as msg_resp:
            res_data = json.loads(msg_resp.read().decode("utf-8"))
            if res_data.get("ok"):
                return {"ok": True, "message": f"Kết nối Telegram thành công! Đã gửi tin nhắn test đến Bot @{username}."}
            else:
                return {"ok": False, "message": f"Telegram API từ chối: {res_data.get('description')}"}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        return {"ok": False, "message": f"Lỗi Telegram HTTP {e.code}: {err_body[:200]}"}
    except Exception as e:
        return {"ok": False, "message": f"Lỗi kết nối Telegram: {str(e)}"}


def send_telegram_report(symbol: str, date: str, bot_token: str = None, chat_id: str = None) -> dict:
    cfg = load_user_config()
    tg_cfg = cfg.get("telegram", {})
    bot_token = (bot_token or tg_cfg.get("bot_token", "")).strip()
    chat_id = (chat_id or tg_cfg.get("chat_id", "")).strip()
    if not bot_token or not chat_id:
        return {"ok": False, "message": "Chưa cấu hình Telegram Bot Token hoặc Chat ID trong Cài đặt."}

    sym = clean_symbol(symbol)
    folder = RESULTS_DIR / sym
    summary_path = folder / f"summary_vi_{sym}_{date}.json"
    pdf_path = folder / f"Tóm tắt biên bản {sym} {date}.pdf"
    
    s_vi = {}
    if summary_path.exists():
        try:
            s_vi = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    rating_vi = s_vi.get("rating_vi", "Đã hoàn tất")
    action_vi = s_vi.get("trader_action_vi", "Duy trì vị thế phòng thủ, tuân thủ dừng lỗ.")
    
    p_close = "Theo thị trường"
    sl_val = "Chặt chẽ"
    tp_val = "Theo cản/hỗ trợ"
    for r in s_vi.get("summary_table", []):
        m = (r.get("muc") or "").lower()
        c = r.get("con_so") or ""
        if "giá lúc chốt" in m or "đóng cửa" in m:
            p_close = c
        elif "cắt lỗ" in m:
            sl_val = c
        elif "chạm tới" in m or "mục tiêu" in m or "dự phóng" in m or "rơi tới" in m:
            tp_val = c

    msg = (
        f"📊 <b>BIÊN BẢN PHÂN TÍCH AI TRADING</b>\n"
        f"💎 <b>Mã:</b> <code>{sym}</code> | <b>Ngày:</b> <code>{date}</code>\n"
        f"═══════════════════════\n"
        f"🎯 <b>Khuyến nghị:</b> <b>{rating_vi}</b>\n"
        f"💵 <b>Giá lúc chốt:</b> <code>{p_close}</code>\n"
        f"🛡️ <b>Cắt lỗ (SL):</b> <code>{sl_val}</code>\n"
        f"🎯 <b>Mục tiêu / Cản:</b> <code>{tp_val}</code>\n"
        f"📌 <b>Kế hoạch Trader:</b> {action_vi}\n"
        f"═══════════════════════\n"
    )
    items = s_vi.get("easy_explanation_items") or s_vi.get("easy_explanation") or []
    if items:
        msg += "💡 <b>Hành động nhanh cho anh em:</b>\n"
        for it in items[:3]:
            msg += f"• {it}\n"
        msg += "\n"
    msg += "📄 <i>File PDF tóm tắt 3 trang chuẩn 5 Phút Trading đính kèm bên dưới 👇</i>"

    try:
        if pdf_path.exists():
            doc_url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
            pdf_bytes = pdf_path.read_bytes()
            fields = {
                "chat_id": chat_id,
                "caption": msg[:1024],
                "parse_mode": "HTML"
            }
            files = {
                "document": (pdf_path.name, pdf_bytes, "application/pdf")
            }
            body, ct = build_multipart_form(fields, files)
            req = urllib.request.Request(doc_url, data=body, headers={
                "Content-Type": ct,
                "User-Agent": "TradingStudio/1.0"
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                if res_data.get("ok"):
                    return {"ok": True, "message": f"Đã gửi báo cáo & file PDF {pdf_path.name} thành công qua Telegram!"}
                else:
                    return {"ok": False, "message": f"Telegram API lỗi: {res_data.get('description')}"}
        else:
            msg_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = json.dumps({
                "chat_id": chat_id,
                "text": msg,
                "parse_mode": "HTML"
            }).encode("utf-8")
            req = urllib.request.Request(msg_url, data=payload, headers={
                "Content-Type": "application/json",
                "User-Agent": "TradingStudio/1.0"
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                return {"ok": True, "message": "Đã gửi thông báo tóm tắt qua Telegram (Chưa có file PDF)."}
    except Exception as e:
        return {"ok": False, "message": f"Không thể gửi qua Telegram: {str(e)}"}


def run_analysis_worker(params: dict):
    global EXECUTION_STATE
    symbol = clean_symbol(params["symbol"])
    trade_date = params["date"]
    provider = params.get("provider", "google").lower()
    api_key = params.get("api_key", "").strip()
    base_url = params.get("base_url")
    quick_model = params.get("quick_model")
    deep_model = params.get("deep_model")
    analysts = params.get("analysts", ["market", "social", "news"])

    with STATE_LOCK:
        EXECUTION_STATE.update({
            "is_running": True,
            "task_id": f"{symbol}_{trade_date}_{int(time.time())}",
            "symbol": symbol,
            "date": trade_date,
            "provider": provider,
            "current_step": "Khởi tạo môi trường",
            "current_step_vi": "Khởi tạo môi trường AI và kiểm tra tham số",
            "node_history": ["Khởi động"],
            "ai_calls": 0,
            "start_time": time.time(),
            "elapsed_seconds": 0,
            "progress_pct": 5,
            "error": None,
            "result": None,
        })

    # Set up environment variables
    if provider == "google":
        os.environ["GOOGLE_API_KEY"] = api_key
    elif provider == "deepseek":
        os.environ["DEEPSEEK_API_KEY"] = api_key
    elif provider == "openai":
        os.environ["OPENAI_API_KEY"] = api_key
    elif provider == "anthropic":
        os.environ["ANTHROPIC_API_KEY"] = api_key
    elif provider == "custom":
        os.environ["OPENAI_COMPATIBLE_API_KEY"] = api_key
        if base_url:
            os.environ["OPENAI_BASE_URL"] = base_url

    os.environ["PYTHONUTF8"] = "1"
    os.environ["TRADINGAGENTS_LLM_MAX_RETRIES"] = "8"

    symbol_dir = RESULTS_DIR / symbol
    symbol_dir.mkdir(parents=True, exist_ok=True)

    # Callback handler for LangGraph
    try:
        from langchain_core.callbacks import BaseCallbackHandler

        class LiveStreamCallback(BaseCallbackHandler):
            def on_chat_model_start(self, serialized, messages, *, run_id=None, metadata=None, **kw):
                try:
                    md = metadata or {}
                    node = md.get("langgraph_node", "")
                    ns = str(md.get("langgraph_checkpoint_ns", "") or "")
                    parent = ns.split("|")[0].split(":")[0] if ns else ""
                    if node not in STEP_NAMES_VI and parent in STEP_NAMES_VI:
                        node = parent

                    vi_name = STEP_NAMES_VI.get(node, f"Phòng phân tích đang làm việc ({node})" if node else "Đang suy luận...")
                    with STATE_LOCK:
                        EXECUTION_STATE["current_step"] = node or "AI Inference"
                        EXECUTION_STATE["current_step_vi"] = vi_name
                        EXECUTION_STATE["ai_calls"] += 1
                        if vi_name not in EXECUTION_STATE["node_history"]:
                            EXECUTION_STATE["node_history"].append(vi_name)
                        # Estimate progress percentage based on node
                        if "Phòng Kỹ thuật" in vi_name:
                            EXECUTION_STATE["progress_pct"] = 25
                        elif "Phe Mua" in vi_name or "Phe Bán" in vi_name:
                            EXECUTION_STATE["progress_pct"] = 55
                        elif "Trưởng phòng" in vi_name:
                            EXECUTION_STATE["progress_pct"] = 70
                        elif "Rủi ro" in vi_name:
                            EXECUTION_STATE["progress_pct"] = 85
                        elif "Giám đốc" in vi_name:
                            EXECUTION_STATE["progress_pct"] = 95
                except Exception:
                    pass

        cb = LiveStreamCallback()
    except Exception as e:
        cb = None
        print("Langchain callback warning:", e)

    # Build TradingAgents Graph configuration
    try:
        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        cfg = DEFAULT_CONFIG.copy()
        cfg["llm_provider"] = "openai_compatible" if provider == "custom" else provider
        if provider == "google":
            if not quick_model or "2.5" in str(quick_model):
                quick_model = "gemini-3.5-flash-lite"
            if not deep_model or "2.5" in str(deep_model):
                deep_model = "gemini-3.8-flash"
        if quick_model:
            cfg["quick_think_llm"] = quick_model
        if deep_model:
            cfg["deep_think_llm"] = deep_model
        if base_url and provider in ("custom", "openai_compatible"):
            cfg["backend_url"] = base_url

        cfg["results_dir"] = str(symbol_dir / "logs")
        cfg["data_cache_dir"] = str(ROOT_DIR / "cache")
        cfg["memory_log_path"] = str(symbol_dir / "so_nho.md")
        cfg["output_language"] = "English"

        is_crypto = symbol.endswith("-USD")
        is_commodity = symbol.endswith("=F") or symbol.startswith("^")
        selected_analysts = list(analysts)
        if not is_crypto and not is_commodity and "fundamentals" not in selected_analysts:
            selected_analysts.append("fundamentals")

        callbacks = [cb] if cb else []
        ta = TradingAgentsGraph(
            selected_analysts=selected_analysts,
            debug=False,
            config=cfg,
            callbacks=callbacks
        )

        with STATE_LOCK:
            EXECUTION_STATE["current_step_vi"] = "Đang lấy dữ liệu thị trường & bắt đầu phân tích..."
            EXECUTION_STATE["progress_pct"] = 15

        t0 = time.time()
        asset_type = "crypto" if is_crypto else ("commodity" if is_commodity else "stock")
        state, rating = ta.propagate(symbol, trade_date, asset_type=asset_type)
        elapsed_min = (time.time() - t0) / 60

        # Construct structured report
        s = state or {}
        structured_report = {
            "symbol": symbol,
            "date": trade_date,
            "rating": str(rating),
            "elapsed_minutes": round(elapsed_min, 2),
            "ai_calls": EXECUTION_STATE["ai_calls"],
            "provider": provider,
            "final_trade_decision": s.get("final_trade_decision", ""),
            "trader_investment_plan": s.get("trader_investment_plan", ""),
            "investment_plan": s.get("investment_plan", ""),
            "market_report": s.get("market_report", ""),
            "sentiment_report": s.get("sentiment_report", ""),
            "news_report": s.get("news_report", ""),
            "fundamentals_report": s.get("fundamentals_report", ""),
            "investment_debate": (s.get("investment_debate_state") or {}).get("history", ""),
            "risk_debate": (s.get("risk_debate_state") or {}).get("history", ""),
        }

        # Build Markdown content
        md_lines = [
            f"# Báo Cáo Phân Tích AI Trading: {symbol}",
            "",
            f"- **Mã tài sản:** `{symbol}`",
            f"- **Ngày phân tích:** `{trade_date}`",
            f"- **Xếp hạng quyết định:** **{rating}**",
            f"- **Thời gian chạy:** `{elapsed_min:.1f} phút`",
            f"- **Nhà cung cấp AI:** `{provider.upper()}`",
            "",
            "---",
            "",
            "## 1. Quyết định cuối cùng của Giám đốc danh mục (Portfolio Manager)",
            "",
            str(structured_report["final_trade_decision"]).strip(),
            "",
            "## 2. Kế hoạch giao dịch của Trader",
            "",
            str(structured_report["trader_investment_plan"]).strip(),
            "",
            "## 3. Kế hoạch của Trưởng phòng Nghiên cứu",
            "",
            str(structured_report["investment_plan"]).strip(),
            "",
            "## 4. Báo cáo Kỹ thuật (Phòng Kỹ thuật)",
            "",
            str(structured_report["market_report"]).strip(),
            "",
            "## 5. Báo cáo Tâm lý thị trường (Mạng xã hội)",
            "",
            str(structured_report["sentiment_report"]).strip(),
            "",
            "## 6. Báo cáo Tin tức & Kinh tế vĩ mô",
            "",
            str(structured_report["news_report"]).strip(),
            "",
        ]
        if structured_report["fundamentals_report"]:
            md_lines += [
                "## 7. Báo cáo Tài chính doanh nghiệp",
                "",
                str(structured_report["fundamentals_report"]).strip(),
                "",
            ]
        if structured_report["investment_debate"]:
            md_lines += [
                "## 8. Lịch sử tranh biện Phe Mua (Bull) vs Phe Bán (Bear)",
                "",
                str(structured_report["investment_debate"]).strip(),
                "",
            ]
        if structured_report["risk_debate"]:
            md_lines += [
                "## 9. Vòng thẩm định của Hội đồng Rủi ro",
                "",
                str(structured_report["risk_debate"]).strip(),
                "",
            ]

        full_md = "\n".join(md_lines)
        structured_report["markdown"] = full_md

        # Save files
        md_file = symbol_dir / f"Biên bản {symbol} {trade_date}.md"
        json_file = symbol_dir / f"report_{symbol}_{trade_date}.json"
        md_file.write_text(full_md, encoding="utf-8")
        json_file.write_text(json.dumps(structured_report, indent=2, ensure_ascii=False), encoding="utf-8")

        # Automatically synthesize & generate 5 Phút Trading Vietnamese Summary PDF
        try:
            with STATE_LOCK:
                EXECUTION_STATE["current_step_vi"] = "Đang xuất bản PDF Tóm tắt biên bản (5 Phút Trading)..."
            import pdf_generator
            pdf_out = pdf_generator.generate_pdf_for_report(symbol, trade_date, structured_report, api_key=api_key, provider=provider)
            if pdf_out and pdf_out.exists():
                structured_report["has_pdf"] = True
                structured_report["pdf_path"] = str(pdf_out)
                structured_report["pdf_file"] = pdf_out.name
                # Re-save JSON with pdf info
                json_file.write_text(json.dumps(structured_report, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as _pdf_err:
            print("PDF generation warning:", _pdf_err)

        # Automatically send notification & PDF to Telegram if enabled
        try:
            cfg = load_user_config()
            tg_cfg = cfg.get("telegram", {})
            if tg_cfg.get("enabled") and tg_cfg.get("bot_token") and tg_cfg.get("chat_id"):
                with STATE_LOCK:
                    EXECUTION_STATE["current_step_vi"] = "Đang gửi báo cáo & PDF qua Telegram..."
                tg_res = send_telegram_report(symbol, trade_date, bot_token=tg_cfg.get("bot_token"), chat_id=tg_cfg.get("chat_id"))
                print("Auto Telegram dispatch result:", tg_res)
        except Exception as _tg_err:
            print("Telegram sending warning:", _tg_err)

        # Append to jsonl
        with (symbol_dir / "ket_qua.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ma": symbol,
                "ngay": trade_date,
                "rating": str(rating),
                "phut": round(elapsed_min, 2),
                "so_luot_ai": EXECUTION_STATE["ai_calls"],
                "loi": None
            }, ensure_ascii=False) + "\n")

        with STATE_LOCK:
            EXECUTION_STATE.update({
                "is_running": False,
                "current_step": "Hoàn tất",
                "current_step_vi": "Phân tích hoàn tất thành công!",
                "progress_pct": 100,
                "result": structured_report
            })

    except Exception as e:
        err_msg = "".join(traceback.format_exception(e))
        print("Analysis Error:", err_msg)
        with (symbol_dir / "ket_qua.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ma": symbol,
                "ngay": trade_date,
                "rating": "LỖI",
                "phut": round((time.time() - EXECUTION_STATE["start_time"]) / 60, 2),
                "so_luot_ai": EXECUTION_STATE["ai_calls"],
                "loi": err_msg[-500:]
            }, ensure_ascii=False) + "\n")

        with STATE_LOCK:
            EXECUTION_STATE.update({
                "is_running": False,
                "current_step": "Lỗi",
                "current_step_vi": f"Lỗi thực thi: {str(e)[:180]}",
                "error": err_msg[-1200:]
            })


class StudioHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            index_file = ROOT_DIR / "index.html"
            if not index_file.exists():
                index_file = WEB_DIR / "index.html"
            if index_file.exists():
                content = index_file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(content)
                return

        if path == "/api/status":
            with STATE_LOCK:
                if EXECUTION_STATE["is_running"]:
                    EXECUTION_STATE["elapsed_seconds"] = int(time.time() - EXECUTION_STATE["start_time"])
                data = dict(EXECUTION_STATE)
                data["repo_path"] = str(ROOT_DIR)
                data["python_version"] = sys.version
            self._send_json(data)
            return

        elif path == "/api/config":
            self._send_json(load_user_config())
            return

        elif path == "/api/history":
            self._send_json({"history": scan_history()})
            return

        elif path == "/api/report":
            symbol = query.get("symbol", [""])[0]
            date = query.get("date", [""])[0]
            if not symbol or not date:
                self._send_json({"error": "Thiếu tham số symbol hoặc date"}, status=400)
            else:
                self._send_json(load_report_detail(symbol, date))
            return

        elif path == "/api/download_pdf":
            symbol = query.get("symbol", [""])[0]
            date = query.get("date", [""])[0]
            view_mode = query.get("view", [""])[0]
            sym = clean_symbol(symbol)
            folder = RESULTS_DIR / sym
            pdf_path = folder / f"Tóm tắt biên bản {sym} {date}.pdf"
            alt_path = folder / f"Tóm tắt biên bản {sym} {date} (Mới).pdf"
            summary_path = folder / f"summary_vi_{sym}_{date}.json"
            json_path = folder / f"report_{sym}_{date}.json"
            
            # Check if PDF is missing or older than summary JSON
            needs_refresh = not pdf_path.exists()
            if not needs_refresh and summary_path.exists():
                try:
                    if pdf_path.stat().st_mtime < summary_path.stat().st_mtime:
                        needs_refresh = True
                except Exception:
                    pass

            if needs_refresh:
                if json_path.exists() or summary_path.exists():
                    try:
                        import pdf_generator
                        rep_data = json.loads(json_path.read_text(encoding="utf-8")) if json_path.exists() else {}
                        if summary_path.exists():
                            rep_data["summary_vi"] = json.loads(summary_path.read_text(encoding="utf-8"))
                        cfg = load_user_config()
                        prov = rep_data.get("provider", "google")
                        key = cfg.get("keys", {}).get(prov) or cfg.get("keys", {}).get("google")
                        gen_res = pdf_generator.generate_pdf_for_report(sym, date, rep_data, api_key=key, provider=prov)
                        if gen_res and gen_res.exists():
                            pdf_path = gen_res
                    except Exception as e:
                        print("PDF refresh error in download_pdf:", e)

            # Check if alt_path exists and is newer
            if alt_path.exists():
                if not pdf_path.exists() or alt_path.stat().st_mtime > pdf_path.stat().st_mtime:
                    pdf_path = alt_path

            if pdf_path and pdf_path.exists():
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                safe_name = f"Tom_tat_bien_ban_{sym}_{date}.pdf"
                disp = "inline" if view_mode == "inline" else "attachment"
                self.send_header("Content-Disposition", f'{disp}; filename="{safe_name}"')
                self.send_header("Content-Length", str(pdf_path.stat().st_size))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                if self.command != "HEAD":
                    with open(pdf_path, "rb") as f:
                        shutil.copyfileobj(f, self.wfile)
            else:
                self._send_json({"error": f"Chưa có file PDF cho {symbol} ngày {date}"}, status=404)
            return

        elif path == "/api/open_pdf":
            symbol = query.get("symbol", [""])[0]
            date = query.get("date", [""])[0]
            sym = clean_symbol(symbol)
            folder = RESULTS_DIR / sym
            pdf_path = folder / f"Tóm tắt biên bản {sym} {date}.pdf"
            alt_path = folder / f"Tóm tắt biên bản {sym} {date} (Mới).pdf"
            summary_path = folder / f"summary_vi_{sym}_{date}.json"
            json_path = folder / f"report_{sym}_{date}.json"

            needs_refresh = not pdf_path.exists()
            if not needs_refresh and summary_path.exists():
                try:
                    if pdf_path.stat().st_mtime < summary_path.stat().st_mtime:
                        needs_refresh = True
                except Exception:
                    pass

            if needs_refresh:
                if json_path.exists() or summary_path.exists():
                    try:
                        import pdf_generator
                        rep_data = json.loads(json_path.read_text(encoding="utf-8")) if json_path.exists() else {}
                        if summary_path.exists():
                            rep_data["summary_vi"] = json.loads(summary_path.read_text(encoding="utf-8"))
                        cfg = load_user_config()
                        prov = rep_data.get("provider", "google")
                        key = cfg.get("keys", {}).get(prov) or cfg.get("keys", {}).get("google")
                        gen_res = pdf_generator.generate_pdf_for_report(sym, date, rep_data, api_key=key, provider=prov)
                        if gen_res and gen_res.exists():
                            pdf_path = gen_res
                    except Exception as e:
                        print("PDF refresh error in open_pdf:", e)

            if alt_path.exists():
                if not pdf_path.exists() or alt_path.stat().st_mtime > pdf_path.stat().st_mtime:
                    pdf_path = alt_path

            if pdf_path and pdf_path.exists():
                try:
                    os.startfile(str(pdf_path.resolve()))
                    self._send_json({
                        "ok": True,
                        "message": f"Đã mở file PDF: {pdf_path.name}",
                        "pdf_file": pdf_path.name,
                        "view_url": f"/api/download_pdf?symbol={sym}&date={date}&view=inline"
                    })
                except Exception as e:
                    self._send_json({"error": f"Không thể khởi chạy ứng dụng xem PDF: {str(e)}"}, status=500)
            else:
                self._send_json({"error": "File PDF chưa tồn tại hoặc chưa được tạo"}, status=404)
            return

        elif path == "/api/candles":
            symbol = query.get("symbol", ["BTC-USD"])[0]
            period = query.get("period", ["6mo"])[0]
            interval = query.get("interval", ["1d"])[0]
            self._send_json(fetch_candles(symbol, period, interval))
            return

        return super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        try:
            content_len = int(self.headers.get("Content-Length", 0))
            post_body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
            payload = json.loads(post_body)
        except Exception as e:
            self._send_json({"error": f"JSON parse error: {str(e)}"}, status=400)
            return

        if path == "/api/save_config":
            save_user_config(payload)
            self._send_json({"ok": True, "message": "Đã lưu cấu hình thành công!"})
            return

        elif path == "/api/test_key":
            provider = payload.get("provider", "google")
            api_key = payload.get("api_key", "")
            base_url = payload.get("base_url", "")
            res = test_api_key(provider, api_key, base_url)
            self._send_json(res)
            return

        elif path == "/api/start":
            with STATE_LOCK:
                if EXECUTION_STATE["is_running"]:
                    self._send_json({"error": "Hệ thống đang có tác vụ phân tích chạy dở. Vui lòng đợi hoàn tất."}, status=409)
                    return

            # Start thread
            t = threading.Thread(target=run_analysis_worker, args=(payload,), daemon=True)
            t.start()
            self._send_json({"ok": True, "message": "Đã bắt đầu phân tích đa tác tử trong nền!"})
            return

        elif path == "/api/test_telegram":
            bot_token = payload.get("bot_token", "")
            chat_id = payload.get("chat_id", "")
            res = test_telegram_connection(bot_token, chat_id)
            self._send_json(res)
            return

        elif path == "/api/send_telegram":
            symbol = payload.get("symbol", "")
            date = payload.get("date", "")
            bot_token = payload.get("bot_token")
            chat_id = payload.get("chat_id")
            res = send_telegram_report(symbol, date, bot_token=bot_token, chat_id=chat_id)
            self._send_json(res)
            return

        self._send_json({"error": "Endpoint not found"}, status=404)

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(body)


def run_server(port=8888):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), StudioHTTPRequestHandler)
    print(f"\n=======================================================")
    print(f"  AI TRADING STUDIO - APPLE DESIGN EDITION")
    print(f"  Website đang chạy tại: http://127.0.0.1:{port}")
    print(f"  Bấm Ctrl+C để dừng máy chủ.")
    print(f"=======================================================\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nĐang tắt máy chủ...")
        server.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8888)
    args = parser.parse_args()
    run_server(args.port)

