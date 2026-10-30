"""Chay TradingAgents (ban v0.5.2) bang Gemini cho 1 ma va 1 ngay, ghi bien ban + trang tien do.
Qua tang tu kenh 5 Phut Trading. Video de nghien cuu, KHONG phai loi khuyen dau tu.
File nay duoc file "Chay AI Trading - 5 Phut Trading.bat" tu tao ra, ban khong can sua.
"""
import argparse, json, os, sys, time, traceback, html
from datetime import datetime
from pathlib import Path

# --- Sua loi: thu muc co dau tieng Viet lam curl (yfinance) khong doc duoc file chung chi ---
import os as _os, shutil as _sh
from pathlib import Path as _P
try:
    import certifi as _cf
    _goc = _P(_os.environ.get("PUBLIC", r"C:\Users\Public")) / "TradingAgents-cert"
    if all(ord(_c) < 128 for _c in str(_goc)):
        _dich = _goc / "cacert.pem"
        _dich.parent.mkdir(parents=True, exist_ok=True)
        if not _dich.exists() or _dich.stat().st_size != _P(_cf.where()).stat().st_size:
            _sh.copyfile(_cf.where(), _dich)
        _cf.where = lambda: str(_dich)
        for _k in ("CURL_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
            _os.environ[_k] = str(_dich)
except Exception as _e:
    print("Canh bao: khong sua duoc duong dan chung chi:", _e)
# --- het phan sua ---

GOC = Path(__file__).resolve().parent
KQ = GOC / "Kết quả"
KQ.mkdir(exist_ok=True)

# Doc khoa Gemini (file .bat da hoi va luu khoa vao day)
_k = GOC / "khoa-gemini.txt"
if _k.exists():
    _d = _k.read_text(encoding="utf-8-sig").strip()
    if _d:
        os.environ.setdefault("GOOGLE_API_KEY", _d)
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("TRADINGAGENTS_LLM_MAX_RETRIES", "10")  # Gemini bao ban (loi 429) thi tu thu lai
if not os.environ.get("GOOGLE_API_KEY"):
    print("CHUA CO KHOA GEMINI. Tat cua so nay roi bam dup lai file .bat de dan khoa.")
    sys.exit(1)

TEN_BUOC = {
    "Market Analyst": "Phòng kỹ thuật (nến + chỉ báo)",
    "Social Analyst": "Phòng tâm lý", "Sentiment Analyst": "Phòng tâm lý",
    "News Analyst": "Phòng tin tức", "Fundamentals Analyst": "Phòng tài chính",
    "Memory Log": "Sổ nhớ chấm lệnh cũ",
    "Bull Researcher": "Phe Mua đang nói", "Bear Researcher": "Phe Bán đang nói",
    "Research Manager": "Trưởng phòng chốt kế hoạch", "Trader": "Trader ra lệnh",
    "Aggressive Analyst": "Rủi ro: người Liều", "Conservative Analyst": "Rủi ro: người Thận trọng",
    "Neutral Analyst": "Rủi ro: người Trung lập", "Portfolio Manager": "Quyết định cuối",
}


def ten_sach(ma):
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in ma)


def cau_hinh(ma):
    from tradingagents.default_config import DEFAULT_CONFIG
    c = DEFAULT_CONFIG.copy()
    c["llm_provider"] = "google"
    c["quick_think_llm"] = os.getenv("GEMINI_QUICK", "gemini-3.5-flash-lite")
    c["deep_think_llm"] = os.getenv("GEMINI_DEEP", "gemini-3.8-flash")
    thu_muc = KQ / ten_sach(ma)
    thu_muc.mkdir(exist_ok=True)
    c["results_dir"] = str(thu_muc / "logs")
    c["data_cache_dir"] = str(GOC / "cache")
    c["memory_log_path"] = str(thu_muc / "so_nho.md")
    c["output_language"] = "English"
    return c, thu_muc


class TheoDoi:
    def __init__(self, thu_muc, ma, ngay):
        self.thu_muc, self.ma, self.ngay = thu_muc, ma, ngay
        self.bat_dau = time.time()
        self.buoc = "Khởi động"; self.so_luot_ai = 0; self.ket_qua = ""
        self.ghi()

    def ghi(self):
        phut = (time.time() - self.bat_dau) / 60
        trang = f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta http-equiv="refresh" content="20">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Tiến độ AI Trading</title>
<style>body{{background:#111;color:#eee;font:17px system-ui;padding:16px;max-width:720px;margin:auto}}h1{{color:#e8b84a;font-size:24px}}
.o{{background:#1d1d24;border-radius:10px;padding:14px 18px;margin:12px 0;line-height:1.7}}b{{color:#fff}}.v{{color:#9b7bff}}</style></head><body>
<h1>Tiến độ AI Trading · 5 Phút Trading</h1>
<div class="o">Mã: <b>{html.escape(self.ma)}</b> · Ngày: <b>{self.ngay}</b> · Bộ não: <b>Gemini</b></div>
<div class="o">Bước hiện tại: <b class="v">{html.escape(self.buoc)}</b><br>Số lượt gọi AI: {self.so_luot_ai} · Đã chạy: {phut:.1f} phút<br>
<small>Trang tự làm mới 20 giây. Cửa sổ đen phải để yên, không tắt.</small></div>
{f'<div class="o">Kết quả: <b class="v">{html.escape(self.ket_qua)}</b><br>Biên bản đã lưu trong thư mục Kết quả.</div>' if self.ket_qua else ''}
</body></html>"""
        (self.thu_muc / "Tiến độ.html").write_text(trang, encoding="utf-8")


def tao_callback(td):
    from langchain_core.callbacks import BaseCallbackHandler

    class CB(BaseCallbackHandler):
        def on_chat_model_start(self, serialized, messages, *, run_id=None, metadata=None, **kw):
            try:
                md = metadata or {}
                node = md.get("langgraph_node", "")
                ns = str(md.get("langgraph_checkpoint_ns", "") or "")
                cha = ns.split("|")[0].split(":")[0] if ns else ""
                if node not in TEN_BUOC and cha in TEN_BUOC:
                    node = cha
                td.buoc = TEN_BUOC.get(node, "Phòng phân tích đang làm việc" if node in ("agent", "tools", "wrap_up") else (node or td.buoc))
                td.so_luot_ai += 1
                td.ghi()
            except Exception:
                pass
    return CB()


def ghi_bien_ban(thu_muc, ma, ngay, state, rating, phut):
    s = state or {}
    phan = [
        ("Quyết định cuối của giám đốc", s.get("final_trade_decision", "")),
        ("Lệnh của nhân viên giao dịch", s.get("trader_investment_plan", "")),
        ("Kế hoạch của trưởng phòng nghiên cứu", s.get("investment_plan", "")),
        ("Phòng kỹ thuật", s.get("market_report", "")),
        ("Phòng tâm lý (mạng xã hội)", s.get("sentiment_report", "")),
        ("Phòng tin tức", s.get("news_report", "")),
        ("Phòng tài chính", s.get("fundamentals_report", "")),
        ("Phe Mua và phe Bán tranh luận", (s.get("investment_debate_state") or {}).get("history", "")),
        ("Vòng quản lý rủi ro", (s.get("risk_debate_state") or {}).get("history", "")),
    ]
    noi_dung = [f"# Biên bản {ma} ngày {ngay}", "",
                f"Xếp hạng: **{rating}** · Thời gian chạy: {phut:.1f} phút · Bộ não: Gemini", "",
                "Biên bản do AI viết bằng tiếng Anh. Chỉ để nghiên cứu, KHÔNG phải lời khuyên đầu tư.", ""]
    for tieu_de, nd in phan:
        if nd and str(nd).strip():
            noi_dung += [f"## {tieu_de}", "", str(nd).strip(), ""]
    tep = thu_muc / f"Biên bản {ten_sach(ma)} {ngay}.md"
    tep.write_text("\n".join(noi_dung), encoding="utf-8")
    return tep


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ma", required=True)
    p.add_argument("--ngay", required=True)
    a = p.parse_args()
    ma = a.ma.strip().upper()
    try:
        datetime.strptime(a.ngay, "%Y-%m-%d")
    except ValueError:
        print(f"Ngay '{a.ngay}' sai dinh dang. Go dang NAM-THANG-NGAY, vi du 2026-10-20.")
        sys.exit(2)

    from tradingagents.graph.trading_graph import TradingAgentsGraph
    c, thu_muc = cau_hinh(ma)
    crypto = ma.endswith("-USD")
    hang_hoa = ma.endswith("=F") or ma.startswith("^")
    phong = ["market", "social", "news"]
    if not crypto and not hang_hoa:
        phong.append("fundamentals")  # co phieu thi bat them phong doc bao cao tai chinh
    td = TheoDoi(thu_muc, ma, a.ngay)
    try:
        import webbrowser
        webbrowser.open((thu_muc / "Tiến độ.html").as_uri())
    except Exception:
        pass
    ta = TradingAgentsGraph(selected_analysts=phong, debug=False, config=c, callbacks=[tao_callback(td)])
    t0 = time.time()
    try:
        state, rating = ta.propagate(ma, a.ngay, asset_type="crypto" if crypto else "stock")
        loi = None
    except Exception as e:
        state, rating, loi = {}, "LOI", "".join(traceback.format_exception(e))[-3000:]
    phut = (time.time() - t0) / 60
    with (thu_muc / "ket_qua.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ma": ma, "ngay": a.ngay, "rating": str(rating), "phut": round(phut, 2),
                            "so_luot_ai": td.so_luot_ai, "loi": loi}, ensure_ascii=False) + "\n")
    if loi:
        if "API_KEY_INVALID" in loi or "API key not valid" in loi:
            td.buoc = "LỖI: khóa Gemini sai, quay lại cửa sổ đen để dán khóa mới"; td.ghi()
            print("\nKHOA GEMINI KHONG DUNG. Quay lai cua so nay de dan khoa moi.")
            sys.exit(4)
        td.buoc = "LỖI, xem cửa sổ đen"; td.ghi()
        print("\nCO LOI KHI CHAY. Thuong gap: ma khong co tren Yahoo Finance, khoa Gemini sai, hoac mat mang.")
        print(loi[-800:])
        sys.exit(3)
    tep = ghi_bien_ban(thu_muc, ma, a.ngay, state, rating, phut)
    td.buoc = "HOÀN THÀNH"; td.ket_qua = str(rating); td.ghi()
    print(f"\nXONG: {ma} ngay {a.ngay} -> {rating} ({phut:.1f} phut)")
    print(f"Bien ban: {tep}")
    print("Bien ban dang mo bang Notepad. Doc dong 'Xep hang' va phan 'Quyet dinh cuoi cua giam doc' truoc.")
    try:
        import subprocess
        subprocess.Popen(["notepad.exe", str(tep)])
    except Exception:
        pass


if __name__ == "__main__":
    main()
