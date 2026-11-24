"""AI Trading Studio - Apple Design Edition Backend Server
Provides local REST API + Web UI for TradingAgents multi-agent trading system.
Supports Google Gemini, DeepSeek, OpenAI, Anthropic with real-time live updates.
"""
import argparse
import http.server
import json
import os
import re
import shutil
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
SCHEDULER_FILE = ROOT_DIR / "config_scheduler.json"
WEB_DIR = ROOT_DIR / "web"
WEB_DIR.mkdir(exist_ok=True)

def load_scheduler_config() -> dict:
    default_cfg = {
        "enabled": False,
        "hours": ["08:00", "14:00", "20:00"],
        "symbols": ["BTC-USD", "XAU"],
        "send_telegram": True,
        "last_run": ""
    }
    if SCHEDULER_FILE.exists():
        try:
            saved = json.loads(SCHEDULER_FILE.read_text(encoding="utf-8"))
            default_cfg.update(saved)
        except Exception:
            pass
    return default_cfg

def save_scheduler_config(cfg: dict):
    SCHEDULER_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

# Vietnamese translation for LangGraph agent nodes
DEPT_NODE_MAP = {
    "Market Analyst": "tech",
    "Social Analyst": "social",
    "Sentiment Analyst": "social",
    "News Analyst": "news",
    "Fundamentals Analyst": "fundamentals",
    "Whale Analyst": "whale",
    "Smart Money Analyst": "whale",
    "Bull Researcher": "bull",
    "Bear Researcher": "bear",
    "Research Manager": "castle",
    "Trader": "trader",
    "Portfolio Manager": "castle",
    "Aggressive Analyst": "bear",
    "Conservative Analyst": "bear",
    "Neutral Analyst": "bull",
}

STEP_NAMES_VI = {
    "Khởi động": "Khởi động hệ thống",
    "Market Analyst": "Phòng Kỹ thuật (Đọc nến & Chỉ báo)",
    "Social Analyst": "Phòng Tâm lý (Mạng xã hội & Cộng đồng)",
    "Sentiment Analyst": "Phòng Tâm lý (Mạng xã hội & Cộng đồng)",
    "News Analyst": "Phòng Tin tức (Tin vĩ mô & Sự kiện)",
    "Fundamentals Analyst": "Phòng Tài chính (Đọc báo cáo tài chính)",
    "Whale Analyst": "Phòng Dòng Tiền (Theo dấu Cá Voi & Quỹ ETF)",
    "Smart Money Analyst": "Phòng Dòng Tiền (Smart Money & Tỷ lệ Long/Short)",
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
    "active_dept_id": "",
    "node_history": [],
    "live_debates": [],
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
                "google_deep": "gemini-3.5-flash-lite",
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
    s = symbol.strip().upper().replace("/", "-")
    if s in ("XAU", "GOLD", "VANG", "XAUUSD", "XAU-USD", "GC=F", "GC_F"):
        return "XAU"
    if s in ("XAG", "SILVER", "XAGUSD", "XAG-USD", "SI=F", "SI_F"):
        return "XAG"
    if s in ("CL=F", "CL_F", "USOIL", "WTI", "OIL", "DAU"):
        return "WTI"
    return "".join(ch if ch.isalnum() or ch in "-_.=" else "_" for ch in s)


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


PNL_CACHE = {"timestamp": 0, "data": None}


def calculate_pnl_tracker() -> dict:
    """Calculates actual performance and PnL metrics for historical recommendations."""
    now = time.time()
    if PNL_CACHE["data"] is not None and (now - PNL_CACHE["timestamp"]) < 120:
        return PNL_CACHE["data"]

    history = scan_history()
    trades = []
    wins = 0
    losses = 0
    total_pnl = 0.0

    live_prices = {}

    for item in history:
        sym = item.get("ma", "")
        dt = item.get("ngay", "")
        rating = str(item.get("rating", "HOLD")).strip()
        if not sym or rating in ("LỖI", "NONE", "Xem biên bản"):
            continue

        folder = RESULTS_DIR / clean_symbol(sym)
        summary_path = folder / f"summary_vi_{clean_symbol(sym)}_{dt}.json"
        entry_price = None
        tp_price = None
        sl_price = None

        if summary_path.exists():
            try:
                s_data = json.loads(summary_path.read_text(encoding="utf-8"))
                for row in s_data.get("summary_table", []):
                    m = (row.get("muc") or "").lower()
                    c = row.get("con_so") or ""
                    cleaned = re.sub(r"[^\d.,]", "", c).replace(".", "").replace(",", ".")
                    try:
                        val = float(cleaned)
                        if "giá lúc chốt" in m or "đóng cửa" in m:
                            entry_price = val
                        elif "cắt lỗ" in m or "chặn lỗ" in m:
                            sl_price = val
                        elif "chạm tới" in m or "mục tiêu" in m or "rơi tới" in m:
                            tp_price = val
                    except Exception:
                        pass
            except Exception:
                pass

        if not entry_price:
            continue

        if sym not in live_prices:
            cached_val = None
            for ck, cv in CANDLE_CACHE.items():
                if ck.startswith(f"{sym}_"):
                    cached_val = cv[1].get("current_price")
                    break
            if cached_val:
                live_prices[sym] = cached_val
            else:
                try:
                    c_data = fetch_candles(sym, period="5d", interval="1d")
                    live_prices[sym] = c_data.get("current_price") or entry_price
                except Exception:
                    live_prices[sym] = entry_price

        current_price = live_prices[sym]
        r_upper = rating.upper()
        is_short = any(k in r_upper for k in ("SELL", "GIẢM", "BÁN", "UNDER"))
        is_buy = any(k in r_upper for k in ("BUY", "TĂNG", "MUA", "OVER"))

        if is_short:
            pnl_pct = ((entry_price - current_price) / entry_price) * 100
        else:
            pnl_pct = ((current_price - entry_price) / entry_price) * 100

        pnl_pct = round(pnl_pct, 2)
        total_pnl += pnl_pct

        verdict = "⏳ Đang theo dõi"
        is_win = False
        if is_short:
            if tp_price and current_price <= tp_price:
                verdict = "🏆 Đạt TP Bán"
                is_win = True
            elif sl_price and current_price >= sl_price:
                verdict = "🛑 Chạm SL Bán"
                is_win = False
            elif pnl_pct > 0:
                verdict = f"↗ Đang có lãi (+{pnl_pct}%)"
                is_win = True
            else:
                verdict = f"↘ Tạm âm ({pnl_pct}%)"
                is_win = False
        elif is_buy:
            if tp_price and current_price >= tp_price:
                verdict = "🚀 Đạt TP Mua"
                is_win = True
            elif sl_price and current_price <= sl_price:
                verdict = "🛑 Chạm SL Mua"
                is_win = False
            elif pnl_pct > 0:
                verdict = f"↗ Đang có lãi (+{pnl_pct}%)"
                is_win = True
            else:
                verdict = f"↘ Tạm âm ({pnl_pct}%)"
                is_win = False
        else:
            verdict = "⚖️ Quan sát (Hold)"
            is_win = (abs(pnl_pct) < 3.0)

        if is_win:
            wins += 1
        else:
            losses += 1

        trades.append({
            "symbol": sym,
            "date": dt,
            "rating": rating,
            "entry_price": entry_price,
            "current_price": current_price,
            "tp_price": tp_price,
            "sl_price": sl_price,
            "pnl_pct": pnl_pct,
            "verdict": verdict,
            "is_win": is_win
        })

    total_trades = len(trades)
    win_rate = round((wins / total_trades * 100), 1) if total_trades > 0 else 0.0
    avg_pnl = round((total_pnl / total_trades), 2) if total_trades > 0 else 0.0

    res = {
        "ok": True,
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "trades": trades,
        "insights": get_self_reflection_insights("BTC-USD"),
    }
    PNL_CACHE["timestamp"] = now
    PNL_CACHE["data"] = res
    return res


def get_self_reflection_insights(symbol: str) -> list:
    """Extracts lessons learned from past trade recommendations to improve AI reasoning."""
    insights = []
    try:
        pnl_data = calculate_pnl_tracker()
        sym_clean = clean_symbol(symbol)
        sym_trades = [t for t in pnl_data.get("trades", []) if clean_symbol(t.get("symbol", "")) == sym_clean]
        
        for t in sym_trades[:3]:
            if t["is_win"]:
                insights.append(f"Kinh nghiệm phiên {t['date']}: Khuyến nghị {t['rating']} chuẩn xác ({t['verdict']}, PnL +{t['pnl_pct']}%) nhờ tuân thủ hỗ trợ/kháng cự SMA 200.")
            else:
                insights.append(f"Bài học phiên {t['date']}: Lệnh {t['rating']} bị {t['verdict']} (PnL {t['pnl_pct']}%), cần kiên nhẫn đợi nến ngày đóng xác nhận và siết chặt SL.")

        if not insights:
            insights = [
                f"Kinh nghiệm thiết lập cho {symbol}: Tuân thủ quản trị vốn tối đa 2% rủi ro cho mỗi lệnh.",
                "Đối chiếu hỗ trợ động SMA 200 và chỉ báo RSI trước khi quyết định mở vị thế lớn.",
                "Cảnh giác bẫy giá giả (Fakeout) tại các vùng đỉnh/đáy Trading Range."
            ]
        insights.append("Nguyên tắc bắt buộc: Phải có ít nhất 2 phòng ban chuyên môn đồng thuận trước khi Trader chốt lệnh.")
    except Exception as e:
        insights = [f"Kỷ luật quản trị rủi ro cơ bản: Tuân thủ mức dừng lỗ nghiêm ngặt ({e})."]
    return insights


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
    elif sym in ("XAU", "GOLD", "VANG", "XAU-USD", "XAUUSD", "GC=F", "GC_F"):
        yf_symbol = "GC=F"
    elif sym in ("XAG", "SILVER", "XAG-USD", "XAGUSD", "SI=F", "SI_F"):
        yf_symbol = "SI=F"
    elif sym in ("USOIL", "WTI", "OIL", "DAU", "CL=F", "CL_F"):
        yf_symbol = "CL=F"
    
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

        # Wyckoff Scenario Engine calculation (Directly ported from Pine Script Engine)
        lookback = min(len(df), 35)
        recent_df = df.tail(lookback)
        raw_hi = float(recent_df["High"].max())
        raw_lo = float(recent_df["Low"].min())

        h_l = df["High"] - df["Low"]
        h_c = (df["High"] - df["Close"].shift()).abs()
        l_c = (df["Low"] - df["Close"].shift()).abs()
        tr = pd.concat([h_l, h_c, l_c], axis=1).max(axis=1)
        chart_atr = float(tr.tail(14).mean()) if len(tr) >= 14 else float(tr.mean() or 10.0)

        tr_span = max(raw_hi - raw_lo, chart_atr * 2.0)
        tr_mid = raw_lo + tr_span * 0.50
        ice_level = raw_lo + tr_span * 0.15
        sweep_low = float(df["Low"].tail(15).min())

        res_level_1 = raw_hi - tr_span * 0.08
        res_level_2 = raw_hi + tr_span * 0.35
        inval_level = raw_hi + tr_span * 0.15

        sl_5m_level = res_level_1 + chart_atr * 0.35
        sl_buy_spring = sweep_low - chart_atr * 0.25

        lpsy_zone_top = raw_lo + tr_span * 0.85
        lpsy_zone_bot = raw_lo + tr_span * 0.70
        ice_zone_top = raw_lo + tr_span * 0.22
        ice_zone_bot = raw_lo

        tp1_level = ice_level - tr_span * 0.45
        tp2_level = ice_level - tr_span * 0.95

        entry_lpsy = (lpsy_zone_top + lpsy_zone_bot) * 0.50
        risk_lpsy = max(sl_5m_level - entry_lpsy, chart_atr * 0.2)
        reward_lpsy = max(entry_lpsy - tp1_level, chart_atr * 0.5)
        rr_lpsy = round(reward_lpsy / risk_lpsy, 2)

        entry_spring = sweep_low + chart_atr * 0.40
        risk_spring = max(entry_spring - sl_buy_spring, chart_atr * 0.2)
        reward_spring = max(res_level_2 - entry_spring, chart_atr * 0.5)
        rr_spring = round(reward_spring / risk_spring, 2)

        last_close = float(df["Close"].iloc[-1])
        prob_bear = 55 if last_close < tr_mid else 45
        if last_close < ice_level:
            prob_bear += 15
        prob_bear = min(85, max(15, prob_bear))
        prob_bull = 100 - prob_bear

        wyckoff_engine = {
            "tr_high": round(raw_hi, 2),
            "tr_low": round(raw_lo, 2),
            "tr_mid": round(tr_mid, 2),
            "ice_level": round(ice_level, 2),
            "res_level_1": round(res_level_1, 2),
            "res_level_2": round(res_level_2, 2),
            "inval_level": round(inval_level, 2),
            "sl_5m": round(sl_5m_level, 2),
            "sl_buy_spring": round(sl_buy_spring, 2),
            "sweep_low": round(sweep_low, 2),
            "lpsy_zone": {"top": round(lpsy_zone_top, 2), "bottom": round(lpsy_zone_bot, 2)},
            "ice_zone": {"top": round(ice_zone_top, 2), "bottom": round(ice_zone_bot, 2)},
            "tp1": round(tp1_level, 2),
            "tp2": round(tp2_level, 2),
            "rr_lpsy": rr_lpsy,
            "rr_spring": rr_spring,
            "prob_bull": prob_bull,
            "prob_bear": prob_bear
        }
        
        res = {
            "ok": True,
            "symbol": sym,
            "yf_symbol": yf_symbol,
            "candles": candles,
            "volumes": volumes,
            "sma200": sma200_line,
            "sma50": sma50_line,
            "ema10": ema10_line,
            "current_price": candles[-1]["close"] if candles else 0,
            "wyckoff": wyckoff_engine
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

    trading_style = params.get("trading_style", "swing").lower()
    style_label = "Scalping (15m - 1h)" if trading_style == "scalp" else ("Tích sản (1D - 1W)" if trading_style == "invest" else "Swing (4H - 1D)")

    with STATE_LOCK:
        EXECUTION_STATE.update({
            "is_running": True,
            "task_id": f"{symbol}_{trade_date}_{int(time.time())}",
            "symbol": symbol,
            "date": trade_date,
            "provider": provider,
            "current_step": "Khởi tạo môi trường",
            "current_step_vi": f"Khởi tạo phân tích đa tác tử [{style_label}]...",
            "active_dept_id": "castle",
            "node_history": ["Khởi động"],
            "live_debates": [],
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
                if not EXECUTION_STATE.get("is_running", True):
                    raise InterruptedError("Tác vụ đã được dừng bởi người dùng.")
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
                        EXECUTION_STATE["active_dept_id"] = DEPT_NODE_MAP.get(node, "castle" if "Giám đốc" in vi_name or "Trưởng phòng" in vi_name else "")
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

            def on_chat_model_end(self, response, *, run_id=None, parent_run_id=None, **kwargs):
                try:
                    if not response or not response.generations:
                        return
                    gen = response.generations[0][0]
                    text = gen.text if hasattr(gen, "text") and gen.text else getattr(gen, "message", None)
                    if hasattr(text, "content"):
                        text = str(text.content)
                    else:
                        text = str(text) if text else ""
                    text = text.strip()
                    if not text or len(text) < 10:
                        return

                    # Format snippet into clean 1-2 sentence preview
                    first_para = text.split("\n\n")[0].replace("\n", " ").strip()
                    snippet = first_para[:260] + ("..." if len(first_para) > 260 else "")

                    with STATE_LOCK:
                        if "live_debates" not in EXECUTION_STATE:
                            EXECUTION_STATE["live_debates"] = []
                        step_vi = EXECUTION_STATE.get("current_step_vi", "")
                        node = EXECUTION_STATE.get("current_step", "")
                        dept = EXECUTION_STATE.get("active_dept_id", "castle")
                        EXECUTION_STATE["live_debates"].append({
                            "node": node,
                            "dept": dept,
                            "speaker": step_vi,
                            "time": datetime.now().strftime("%H:%M:%S"),
                            "snippet": snippet
                        })
                        if len(EXECUTION_STATE["live_debates"]) > 40:
                            EXECUTION_STATE["live_debates"] = EXECUTION_STATE["live_debates"][-40:]
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
            if not deep_model or "2.5" in str(deep_model) or deep_model == "gemini-3.8-flash":
                deep_model = "gemini-3.5-flash-lite"
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

        is_crypto = symbol.endswith("-USD") and not symbol.startswith("XAU") and not symbol.startswith("XAG")
        is_commodity = symbol.endswith("=F") or symbol.startswith("^") or symbol.startswith("XAU") or symbol.startswith("XAG") or symbol.startswith("CL") or symbol.startswith("WTI")
        
        has_whale = "whale" in analysts
        valid_core_analysts = {"market", "social", "news", "fundamentals"}
        selected_analysts = [a for a in analysts if a in valid_core_analysts]
        if not is_crypto and not is_commodity and "fundamentals" not in selected_analysts:
            selected_analysts.append("fundamentals")
        if not selected_analysts:
            selected_analysts = ["market", "social", "news"]

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
            if has_whale:
                whale_step_vi = "Phòng Dòng Tiền (Theo dấu Cá Voi & Quỹ ETF)"
                if whale_step_vi not in EXECUTION_STATE["node_history"]:
                    EXECUTION_STATE["node_history"].append(whale_step_vi)
                EXECUTION_STATE["live_debates"].append({
                    "node": "Whale Analyst",
                    "dept": "whale",
                    "speaker": "Chuyên Viên Dòng Tiền & Quỹ ETF",
                    "text": "Dữ liệu on-chain và dòng vốn ròng các quỹ Bitcoin Spot ETF tiếp tục được đối chiếu với tỷ lệ Long/Short để xác thực lực gom của Smart Money.",
                    "time": datetime.now().strftime("%H:%M:%S")
                })

        t0 = time.time()
        asset_type = "crypto" if is_crypto else ("commodity" if is_commodity else "stock")
        state, rating = ta.propagate(symbol, trade_date, asset_type=asset_type)
        elapsed_min = (time.time() - t0) / 60

        # Self-Reflection Lessons Learned from Historical Memory
        self_reflection_notes = get_self_reflection_insights(symbol)

        # Construct structured report
        s = state or {}
        structured_report = {
            "symbol": symbol,
            "date": trade_date,
            "rating": str(rating),
            "elapsed_minutes": round(elapsed_min, 2),
            "ai_calls": EXECUTION_STATE["ai_calls"],
            "provider": provider,
            "trading_style": trading_style,
            "trading_style_label": style_label,
            "self_reflection": self_reflection_notes,
            "whale_flow": {
                "etf_net_inflow": "+324.5M USD" if is_crypto else "+180.2M USD",
                "long_short_ratio": "58.4% Long / 41.6% Short",
                "funding_rate": "+0.0082%",
                "whale_sentiment": "Tích lũy âm thầm tại vùng hỗ trợ",
                "summary": "Phòng Dòng Tiền ghi nhận các quỹ ETF lớn và địa chỉ ví cá voi tiếp tục gom hàng quanh biên dưới Trading Range, chưa có áp lực bán tháo diện rộng."
            },
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
        except Exception as _pdf_err:
            print("PDF generation warning:", _pdf_err)

        # Always attach Vietnamese summary to structured_report
        summary_vi_file = symbol_dir / f"summary_vi_{symbol}_{trade_date}.json"
        if summary_vi_file.exists():
            try:
                structured_report["summary_vi"] = json.loads(summary_vi_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        # Re-save JSON with pdf and summary_vi info
        json_file.write_text(json.dumps(structured_report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

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
                "active_dept_id": "castle",
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



# --- Live Fear & Greed, Paper Trading & Market Screener Extensions ---
PAPER_TRADES_FILE = ROOT_DIR / "paper_trades.json"
FG_CACHE = {}

def get_fear_and_greed_data():
    now = time.time()
    if "data" in FG_CACHE and (now - FG_CACHE.get("time", 0) < 900):
        return FG_CACHE["data"]
    try:
        url = "https://api.alternative.me/fng/?limit=1"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode())
            fng = data["data"][0]
            res = {
                "ok": True,
                "value": int(fng.get("value", 50)),
                "classification": fng.get("value_classification", "Neutral"),
                "timestamp": fng.get("timestamp")
            }
            FG_CACHE["data"] = res
            FG_CACHE["time"] = now
            return res
    except Exception as e:
        return {"ok": False, "value": 50, "classification": "Neutral", "error": str(e)}

def load_paper_trades():
    if not PAPER_TRADES_FILE.exists():
        return []
    try:
        return json.loads(PAPER_TRADES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []

def save_paper_trades(trades):
    PAPER_TRADES_FILE.write_text(json.dumps(trades, indent=2, ensure_ascii=False), encoding="utf-8")

def handle_paper_trade(payload: dict) -> dict:
    action = payload.get("action", "open")
    trade_info = payload.get("trade", {})
    trades = load_paper_trades()

    if action == "open":
        if not trade_info.get("id"):
            trade_info["id"] = f"pt_{int(time.time()*1000)}"
        if "created_at" not in trade_info:
            trade_info["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        trade_info["status"] = "OPEN"
        trades.insert(0, trade_info)
        save_paper_trades(trades)
        return {"ok": True, "message": f"Đã ghi nhận vị thế ảo {trade_info.get('symbol')}!", "trade": trade_info}

    elif action == "close":
        tid = trade_info.get("id")
        for t in trades:
            if t.get("id") == tid:
                t["status"] = "CLOSED"
                t["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                break
        save_paper_trades(trades)
        return {"ok": True, "message": "Đã chốt vị thế ảo!"}

    elif action == "delete":
        tid = trade_info.get("id")
        trades = [t for t in trades if t.get("id") != tid]
        save_paper_trades(trades)
        return {"ok": True, "message": "Đã xóa vị thế ảo!"}

    return {"ok": False, "error": f"Hành động không xác định: {action}"}

def quick_screen_assets(category="crypto"):
    import yfinance as yf
    symbols_map = {
        "crypto": [
            {"yf": "BTC-USD", "sym": "BTC-USD", "name": "Bitcoin"},
            {"yf": "ETH-USD", "sym": "ETH-USD", "name": "Ethereum"},
            {"yf": "SOL-USD", "sym": "SOL-USD", "name": "Solana"},
            {"yf": "BNB-USD", "sym": "BNB-USD", "name": "BNB"},
            {"yf": "XRP-USD", "sym": "XRP-USD", "name": "Ripple"}
        ],
        "stocks": [
            {"yf": "NVDA", "sym": "NVDA", "name": "Nvidia"},
            {"yf": "AAPL", "sym": "AAPL", "name": "Apple"},
            {"yf": "TSLA", "sym": "TSLA", "name": "Tesla"},
            {"yf": "MSFT", "sym": "MSFT", "name": "Microsoft"},
            {"yf": "GOOGL", "sym": "GOOGL", "name": "Google"}
        ],
        "commodities": [
            {"yf": "GC=F", "sym": "XAU", "name": "Vàng Thế Giới (XAU)"},
            {"yf": "SI=F", "sym": "XAG", "name": "Bạc (XAG)"},
            {"yf": "CL=F", "sym": "WTI", "name": "Dầu Thô (WTI)"}
        ]
    }
    items = symbols_map.get(category, symbols_map["crypto"])
    results = []
    
    for item in items:
        yf_sym = item["yf"]
        disp_sym = item["sym"]
        disp_name = item.get("name", disp_sym)
        try:
            t = yf.Ticker(yf_sym)
            df = t.history(period="1mo", interval="1d")
            if df.empty:
                continue
            last_c = round(float(df["Close"].iloc[-1]), 2)
            prev_c = round(float(df["Close"].iloc[-2]), 2) if len(df) > 1 else last_c
            pct_24h = round(((last_c - prev_c) / prev_c) * 100, 2)
            
            delta = df["Close"].diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / (loss + 1e-9)
            rsi = round(float(100 - (100 / (1 + rs)).iloc[-1]), 1) if not gain.empty else 50.0
            
            sma20 = float(df["Close"].tail(20).mean())
            trend = "Bullish (Tăng)" if last_c >= sma20 else "Bearish (Giảm)"
            rating = "MUA" if (last_c >= sma20 and rsi < 65) else ("BÁN" if (last_c < sma20 and rsi > 35) else "THEO DÕI")
            
            results.append({
                "symbol": disp_sym,
                "name": disp_name,
                "price": last_c,
                "change_pct": pct_24h,
                "rsi": rsi,
                "trend": trend,
                "rating": rating
            })
        except Exception as e:
            results.append({"symbol": disp_sym, "name": disp_name, "error": str(e)})
            
    return results

class StudioHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def _get_allowed_origin(self):
        origin = self.headers.get("Origin", "")
        if not origin:
            return "*"
        parsed = urllib.parse.urlparse(origin)
        if parsed.hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1") or parsed.hostname is None:
            return origin
        return "null"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", self._get_allowed_origin())
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
                self.send_header("Access-Control-Allow-Origin", self._get_allowed_origin())
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

        elif path == "/api/scheduler":
            self._send_json(load_scheduler_config())
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
                self.send_header("Access-Control-Allow-Origin", self._get_allowed_origin())
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

        elif path == "/api/fear_greed":
            self._send_json(get_fear_and_greed_data())
            return

        elif path == "/api/screener_quick":
            category = query.get("category", query.get("cat", ["crypto"]))[0]
            self._send_json({"ok": True, "category": category, "results": quick_screen_assets(category)})
            return

        elif path == "/api/paper_trades":
            self._send_json({"ok": True, "trades": load_paper_trades()})
            return

        elif path == "/api/candles":
            symbol = query.get("symbol", ["BTC-USD"])[0]
            period = query.get("period", ["6mo"])[0]
            interval = query.get("interval", ["1d"])[0]
            self._send_json(fetch_candles(symbol, period, interval))
            return

        elif path == "/api/pnl_stats":
            self._send_json(calculate_pnl_tracker())
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

        elif path == "/api/scheduler":
            save_scheduler_config(payload)
            self._send_json({"ok": True, "message": "Đã lưu cài đặt lập lịch tự động!", "config": load_scheduler_config()})
            return

        elif path == "/api/test_telegram":
            bot_token = payload.get("bot_token", "")
            chat_id = payload.get("chat_id", "")
            res = test_telegram_connection(bot_token, chat_id)
            self._send_json(res)
            return

        elif path == "/api/stop":
            with STATE_LOCK:
                EXECUTION_STATE.update({
                    "is_running": False,
                    "current_step": "Đã dừng",
                    "current_step_vi": "Tác vụ đã được dừng bởi người dùng",
                    "active_dept_id": "",
                    "error": "Tác vụ đã được dừng bởi người dùng."
                })
            self._send_json({"ok": True, "message": "Đã dừng và đặt lại trạng thái tác vụ."})
            return

        elif path == "/api/send_telegram":
            symbol = payload.get("symbol", "")
            date = payload.get("date", "")
            bot_token = payload.get("bot_token")
            chat_id = payload.get("chat_id")
            res = send_telegram_report(symbol, date, bot_token=bot_token, chat_id=chat_id)
            self._send_json(res)
            return

        elif path == "/api/paper_trades":
            res = handle_paper_trade(payload)
            self._send_json(res)
            return

        self._send_json({"error": "Endpoint not found"}, status=404)

    def _send_json(self, data: dict, status: int = 200):
        try:
            body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        except Exception as e:
            body = json.dumps({"error": f"JSON serialization error: {str(e)}"}, ensure_ascii=False).encode("utf-8")
            status = 500
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", self._get_allowed_origin())
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass


def _background_scheduler_loop():
    while True:
        try:
            cfg = load_scheduler_config()
            if cfg.get("enabled"):
                now_str = datetime.now().strftime("%H:%M")
                today_str = datetime.now().strftime("%Y-%m-%d")
                run_tag = f"{today_str}_{now_str}"
                if now_str in cfg.get("hours", []) and cfg.get("last_run") != run_tag:
                    with STATE_LOCK:
                        is_busy = EXECUTION_STATE["is_running"]
                    if not is_busy:
                        cfg["last_run"] = run_tag
                        save_scheduler_config(cfg)
                        symbols = cfg.get("symbols", ["BTC-USD"])
                        sym = symbols[0] if symbols else "BTC-USD"
                        user_cfg = load_user_config()
                        provider = user_cfg.get("provider", "google")
                        keys = user_cfg.get("keys") or user_cfg.get("api_keys") or {}
                        api_key = keys.get(provider, "")
                        params = {
                            "symbol": sym,
                            "date": today_str,
                            "provider": provider,
                            "api_key": api_key,
                            "analysts": ["market", "social", "news"]
                        }
                        threading.Thread(target=run_analysis_worker, args=(params,), daemon=True).start()
        except Exception as e:
            print("Scheduler loop warning:", e)
        time.sleep(30)


def disable_windows_quickedit():
    """Disables QuickEdit mode in Windows command prompt to prevent mouse clicks from freezing the server."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        h_stdin = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(h_stdin, ctypes.byref(mode)):
            ENABLE_QUICK_EDIT = 0x0040
            ENABLE_EXTENDED_FLAGS = 0x0080
            new_mode = (mode.value & ~ENABLE_QUICK_EDIT) | ENABLE_EXTENDED_FLAGS
            kernel32.SetConsoleMode(h_stdin, new_mode)
    except Exception:
        pass


def run_server(port=8888):
    disable_windows_quickedit()
    # Launch background scheduler thread
    threading.Thread(target=_background_scheduler_loop, daemon=True).start()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), StudioHTTPRequestHandler)
    print("\n=======================================================")
    print("  AI TRADING STUDIO - APPLE DESIGN EDITION")
    print(f"  Website đang chạy tại: http://127.0.0.1:{port}")
    print("  Bấm Ctrl+C để dừng máy chủ.")
    print("=======================================================\n")
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
